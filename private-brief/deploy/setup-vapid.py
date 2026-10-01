"""Root-only, explicit-consent setup. Never prints the private VAPID key."""
import base64
import os
import subprocess
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid02

if os.geteuid()!=0: raise SystemExit('Root required')
os.umask(0o077)
path=Path('/etc/nabi-brief/environment')
lines=path.read_text().splitlines()
env=dict(line.split('=',1) for line in lines if line and not line.startswith('#'))
if env.get('BRIEF_VAPID_PRIVATE_KEY') or env.get('BRIEF_VAPID_PUBLIC_KEY'): raise SystemExit('Existing VAPID identity preserved')
key=Vapid02();key.generate_keys()
encode=lambda b:base64.urlsafe_b64encode(b).decode().rstrip('=')
private=encode(key.private_key.private_bytes(serialization.Encoding.DER,serialization.PrivateFormat.TraditionalOpenSSL,serialization.NoEncryption()))
Vapid02.from_string(private) # Verify the stored form is accepted before persisting.
public=encode(key.public_key.public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint))
new={'BRIEF_VAPID_PRIVATE_KEY':private,'BRIEF_VAPID_PUBLIC_KEY':public,'BRIEF_VAPID_SUBJECT':'https://brief.nabi.be'}
temporary=path.with_name('environment.vapid-next')
if temporary.exists(): raise SystemExit('Staged file already exists')
with temporary.open('x') as f:
    f.write('\n'.join(line for line in lines if not any(line.startswith(k+'=') for k in new))+'\n')
    for k,v in new.items():f.write(k+'='+v+'\n')
temporary.chmod(0o600);os.replace(temporary,path)
subprocess.run(['/bin/systemctl','restart','nabi-brief.service'],check=True)
print('VAPID_CONFIGURED_PHONE_OPT_IN_REQUIRED_NO_NOTIFICATION_SENT')
