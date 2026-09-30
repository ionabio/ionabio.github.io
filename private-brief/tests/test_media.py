import tempfile,unittest
from pathlib import Path
from brief.media import import_image,media_record
from brief.web import create_app
from werkzeug.security import generate_password_hash
class MediaTests(unittest.TestCase):
 def test_topics_and_rights(self):
  self.assertEqual(media_record({'kind':'illustration','theme':'health'})['theme'],'health')
  self.assertEqual(media_record({'kind':'illustration','theme':'invalid'})['theme'],'news')
  with self.assertRaises(ValueError):media_record({'kind':'photo','credit':'Unknown'})
 def test_private_image_boundary(self):
  with tempfile.TemporaryDirectory() as directory:
   config={'TESTING':True,'SECRET_KEY':'synthetic','PASSWORD_HASH':generate_password_hash('synthetic'),'DATABASE':str(Path(directory)/'db.sqlite'),'PUBLIC_ORIGIN':'https://brief.test','MEDIA_DIR':directory}
   app=create_app(config);client=app.test_client();name='a'*64+'.png'
   self.assertEqual(client.get('/media/'+name).status_code,302)
   self.assertEqual(client.get('/media/../../README.md').status_code,302)
   Path(directory,name).write_bytes(b'synthetic image')
   client.get('/login')
   with client.session_transaction() as session:token=session['csrf']
   client.post('/login',data={'password':'synthetic','csrf':token},headers={'Origin':'https://brief.test'})
   response=client.get('/media/'+name)
   self.assertEqual(response.status_code,200);self.assertIn('no-store',response.headers['Cache-Control'])
   response.close()
   self.assertEqual(client.get('/media/not-an-image.png').status_code,404)
 def test_image_type_and_import(self):
  with tempfile.TemporaryDirectory() as directory:
   source=Path(directory)/'source';source.write_bytes(b'<svg><script>bad</script></svg>')
   with self.assertRaises(ValueError):import_image(source,Path(directory)/'media')
   source.write_bytes(b'\x89PNG\r\n\x1a\n' + b'synthetic-test-data')
   result=import_image(source,Path(directory)/'media');self.assertEqual(result['extension'],'png');self.assertEqual(len(result['imageId']),64)
if __name__=='__main__':unittest.main()
