"""Synthetic local-only demo; no real source connections, no outbound push."""
import json
import tempfile
from datetime import datetime
from pathlib import Path
from werkzeug.security import generate_password_hash
from .adapters import digest
from .pipeline import run,clock
from .editorial import reviewed_language
from .store import Store
from .web import create_app

def main():
    root=Path(__file__).resolve().parent.parent
    temp=tempfile.TemporaryDirectory(prefix='nabi-synthetic-demo-')
    store=Store(Path(temp.name)/'demo.sqlite')
    bundle=json.loads((root/'fixtures/synthetic.json').read_text(encoding='utf-8-sig'))
    now=clock();bundle['date']=now.date().isoformat()
    for source in ('calendar','todos','news'):bundle[source]['checkedAt']=now.replace(hour=7,minute=50,second=0).isoformat()
    item=bundle['news']['items'][0]
    item['reviewedLanguage']={'sourceHash':digest(item['authorizedText']),'summaryNl':'Dit is een fictief voorbeeld: buurtbewoners ruilen boeken om elkaar beter te leren kennen.','vocabulary':[{'word':'de boekenruil','meaning':'een moment waarop mensen boeken uitwisselen','example':'Zaterdag gaat onze buurt naar de boekenruil.'},{'word':'samenbrengen','meaning':'mensen met elkaar in contact brengen','example':'De boekenruil brengt de buurt samen.'}],'puzzle':[{'question':'Wat hopen de organisatoren te bereiken?','answer':'Ze hopen mensen samen te brengen.','explanation':'De teaser zegt dat de boekenruil mensen samenbrengt. Wie er precies komt, weten we niet.','evidence':'De organisatoren hopen dat de boekenruil mensen samenbrengt.'}]}
    # Simulated 08:00 for demonstration only; production always uses the real clock.
    run(store,bundle,now.replace(hour=8,minute=0),generator=reviewed_language)
    app=create_app({'TESTING':True,'SECRET_KEY':'synthetic-demo-only-never-deploy','PASSWORD_HASH':generate_password_hash('demo-only'),'DATABASE':store.path,'PUBLIC_ORIGIN':'http://127.0.0.1:8080','SESSION_COOKIE_SECURE':False})
    print('Synthetic preview: http://127.0.0.1:8080 ; password: demo-only',flush=True)
    app.run(host='127.0.0.1',port=8080,debug=False,use_reloader=False)
if __name__=='__main__':main()
