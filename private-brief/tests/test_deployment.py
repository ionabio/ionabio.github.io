import importlib.util
import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

spec=importlib.util.spec_from_file_location('updater',Path(__file__).parents[1]/'deploy/updater.py')
updater=importlib.util.module_from_spec(spec);spec.loader.exec_module(updater)

class DeploymentTests(unittest.TestCase):
    def test_invalid_higher_releases_do_not_hide_verified_approval(self):
        now=datetime.now(timezone.utc);workflow=b'reviewed workflow';payload=b'verified archive'
        def manifest(run):
            return dict(schema=1,repository=updater.REPO,commit='a'*40,workflowCommit='b'*40,runId=run,artifact='brief-arm64.tar.gz',sha256=hashlib.sha256(payload).hexdigest(),issuedAt=now.isoformat(),expiresAt=(now+timedelta(hours=24)).isoformat())
        def release(run):
            return dict(draft=False,prerelease=False,tag_name='brief-production-'+str(run),assets=[{'name':name,'browser_download_url':'https://github.com/'+updater.REPO+'/releases/download/brief-production-'+str(run)+'/'+name} for name in ('approval.json','approval.sigstore.jsonl','brief-arm64.tar.gz')])
        for fault in ('missing_asset','bad_signature','bad_source_signature','expired','wrong_run','workflow_policy','archive_digest'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as tmp:
                work=Path(tmp);pin=work/'workflow.sha256';pin.write_text(hashlib.sha256(workflow).hexdigest())
                bad=release(20);good=release(15);old=release(10);values={20:manifest(20),15:manifest(15)}
                if fault=='missing_asset': bad['assets']=bad['assets'][1:]
                if fault=='expired': values[20]['expiresAt']=(now-timedelta(seconds=1)).isoformat()
                if fault=='wrong_run': values[20]['runId']=21
                if fault=='workflow_policy': values[20]['workflowCommit']='c'*40
                if fault=='archive_digest': values[20]['sha256']='d'*64
                def download(url,target=None,limit=None):
                    if 'raw.githubusercontent.com' in url: return b'unapproved workflow' if '/'+('c'*40)+'/' in url else workflow
                    run=int(url.split('/brief-production-')[1].split('/')[0]);name=url.rsplit('/',1)[1]
                    content=json.dumps(values[run]).encode() if name=='approval.json' else payload
                    target.write_bytes(content)
                calls=[]
                def verify(command,**kwargs):
                    calls.append(command)
                    value=json.loads(Path(command[3]).read_text())
                    if value['runId']==20 and (fault=='bad_signature' or (fault=='bad_source_signature' and '--source-digest' in command)):
                        raise subprocess.CalledProcessError(1,command)
                with patch.object(updater,'download',side_effect=download),patch.object(updater.subprocess,'run',side_effect=verify),patch.object(updater,'WORKFLOW_PIN',pin):
                    selected=updater.select_release([good,old,bad],10,work)
                self.assertEqual(selected[0]['runId'],15);self.assertEqual(selected[1].read_bytes(),payload)
                good_calls=calls[-2:]
                self.assertEqual(len(good_calls),2)
                for command in good_calls:
                    self.assertIn(updater.IDENTITY,command);self.assertIn('--deny-self-hosted-runners',command)
                self.assertIn('--source-ref',good_calls[-1]);self.assertIn('refs/heads/main',good_calls[-1]);self.assertIn('--source-digest',good_calls[-1]);self.assertIn('b'*40,good_calls[-1])
                self.assertFalse((work/'state.json').exists())
    def test_selection_never_replays_or_downgrades(self):
        releases=[dict(draft=False,prerelease=False,tag_name='brief-production-'+str(run)) for run in (9,10)]
        with tempfile.TemporaryDirectory() as tmp,patch.object(updater,'verify_release') as verify:
            self.assertIsNone(updater.select_release(releases,10,Path(tmp)));verify.assert_not_called()
        releases.append(dict(draft=False,prerelease=False,tag_name='brief-production-20'))
        with tempfile.TemporaryDirectory() as tmp,patch.object(updater,'verify_release',side_effect=ValueError('invalid')) as verify:
            self.assertIsNone(updater.select_release(releases,10,Path(tmp)));self.assertEqual(verify.call_count,1)
    def test_release_pagination_keeps_older_candidates_visible(self):
        with patch.object(updater,'download',side_effect=[[{'tag_name':'invalid'}]*100,[{'tag_name':'brief-production-15'}]]) as download:
            self.assertEqual(len(updater.release_list()),101)
        self.assertIn('page=2',download.call_args_list[1].args[0])
    def test_python313_preflight_rejects_missing_or_wrong_interpreter_before_mutation(self):
        for error in (FileNotFoundError(),subprocess.CalledProcessError(1,['python3.13'])):
            with self.subTest(error=type(error).__name__),tempfile.TemporaryDirectory() as tmp:
                state=Path(tmp)/'absent-state'
                with patch.object(updater,'STATE',state),patch.object(updater.platform,'machine',return_value='aarch64'),patch.object(updater.os,'geteuid',return_value=0,create=True),patch.object(updater.subprocess,'run',side_effect=error) as run,patch.object(updater,'release_list') as releases:
                    with self.assertRaisesRegex(RuntimeError,'Python 3.13 required.*production unchanged'):updater.update()
                self.assertFalse(state.exists());releases.assert_not_called();self.assertEqual(run.call_args.args[0][0],'/usr/bin/python3.13')
    def test_bootstrap_python_preflight_before_persistent_operations(self):
        deploy=Path(__file__).parents[1]/'deploy'
        source=(deploy/'bootstrap-updater.sh').read_text()
        preflight=source.split('test "$(id -u)" = 0')[0]
        self.assertIn('/usr/bin/python3.13',preflight);self.assertIn('sys.version_info[:2] == (3,13)',preflight)
        self.assertNotIn('install -d',preflight)
        self.assertIn('ExecStart=/usr/bin/python3.13 ',(deploy/'nabi-release-update.service').read_text())
        if os.name=='posix':
            # Exercise the real early failure branch, without executing bootstrap.
            result=subprocess.run(['sh','-s'],input=preflight.replace('/usr/bin/python3.13','/missing-python313').encode(),capture_output=True)
            self.assertEqual(result.returncode,1);self.assertIn(b'Production unchanged',result.stderr)
    def test_manifest_expiry_and_pin(self):
        now=datetime.now(timezone.utc)
        m=dict(schema=1,repository=updater.REPO,commit='a'*40,workflowCommit='b'*40,runId=10,artifact='brief-arm64.tar.gz',sha256='c'*64,issuedAt=now.isoformat(),expiresAt=(now+timedelta(hours=24)).isoformat())
        updater.manifest_check(m,10,now)
        for key,value in [('commit','main'),('repository','attacker/repo'),('expiresAt',(now-timedelta(seconds=1)).isoformat()),('sha256','invalid')]:
            changed={**m,key:value}
            with self.assertRaises(ValueError): updater.manifest_check(changed,10,now)
        with self.assertRaises(ValueError): updater.manifest_check(m,11,now)
    def test_archive_traversal_and_symlinks_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive=Path(tmp)/'bad.tar.gz'
            for name,kind in [('../private.sqlite3',tarfile.REGTYPE),('/etc/forbidden',tarfile.REGTYPE),('brief/link',tarfile.SYMTYPE),('secrets/token',tarfile.REGTYPE)]:
                with tarfile.open(archive,'w:gz') as tar:
                    item=tarfile.TarInfo(name);item.type=kind;item.linkname='/etc';tar.addfile(item)
                with self.assertRaises(ValueError): updater.extract(archive,Path(tmp)/'out')
    def test_failed_rollout_rolls_back_without_touching_private_data(self):
        if os.name!='posix': self.skipTest('Atomic directory symlink replacement is Linux-only; run on Pi/ARM64 CI')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old';new=root/'new';old.mkdir();new.mkdir();link=root/'current'
            private=root/'private.sqlite3';private.write_bytes(b'preserve-private-data')
            try: link.symlink_to(old,target_is_directory=True)
            except OSError: self.skipTest('Symlinks require Windows developer mode; exercised on Linux/Pi')
            restarts=[]
            def health():return link.resolve()==old
            with self.assertRaises(RuntimeError): updater.rollout(link,new,lambda:restarts.append(link.resolve()),health)
            self.assertEqual(link.resolve(),old);self.assertEqual(restarts,[new,old]);self.assertEqual(private.read_bytes(),b'preserve-private-data')
            updater.rollout(link,new,lambda:None,lambda:True);self.assertEqual(link.resolve(),new)
