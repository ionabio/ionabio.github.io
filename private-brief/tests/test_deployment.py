import importlib.util
import io
import json
import os
import tarfile
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

spec=importlib.util.spec_from_file_location('updater',Path(__file__).parents[1]/'deploy/updater.py')
updater=importlib.util.module_from_spec(spec);spec.loader.exec_module(updater)

class DeploymentTests(unittest.TestCase):
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
            for name,kind in [('../private.sqlite3',tarfile.REGTYPE),('/etc/password',tarfile.REGTYPE),('brief/link',tarfile.SYMTYPE),('secrets/token',tarfile.REGTYPE)]:
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
