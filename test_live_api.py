"""Teste explícito com rede: baixa MP3 pela API e verifica áudio e JSON persistidos."""
import json
import subprocess
from pathlib import Path
from fastapi.testclient import TestClient
import api
URL="https://soundcloud.com/dilxcoff/montagem-alquimia-2-slowed-2"
with TestClient(api.app) as client:
    for endpoint in ['/scrape/raw','/track','/comments']:
        response=client.get(endpoint,params={'url':URL})
        assert response.status_code==200,(endpoint,response.text)
        data=response.json()
        if endpoint=='/scrape/raw': assert data['raw_hydration'] and not data['warnings']
        if endpoint=='/comments': assert data['count'] > 0 and not data['warnings']
        print(endpoint,'OK',flush=True)
    response=client.post('/download',json={'url':URL})
    assert response.status_code==200,response.text
    result=response.json()
    assert not result['warnings']
    file=client.get(result['download']['url'])
    assert file.status_code==200 and file.headers['content-type']=='audio/mpeg'
    assert len(file.content)==result['download']['size_bytes']
    partial=client.get(result['download']['url'],headers={'Range':'bytes=0-1023'})
    assert partial.status_code==206 and len(partial.content)==1024
    path=api.DOWNLOADS/(result['download']['id']+'.mp3')
    assert json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))==result
    probe=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration,size:stream=codec_name','-of','json',str(path)],capture_output=True,text=True,check=True)
    info=json.loads(probe.stdout)
    assert info['streams'][0]['codec_name']=='mp3'
    assert abs(float(info['format']['duration'])-result['track']['duration_ms']/1000)<1
    decode=subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(path),'-f','null','-'],capture_output=True,text=True)
    assert decode.returncode==0 and not decode.stderr,decode.stderr
    Path('api-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    Path('test-report.json').write_text(json.dumps({'track':result['track']['title'],'comments':len(result['comments']),'waveform':bool(result['waveform']),'warnings':result['warnings'],'audio':info,'decode_ok':True,'range_ok':True,'saved_json_ok':True},ensure_ascii=False,indent=2),encoding='utf-8')
    print('DOWNLOAD OK:',result['download'],flush=True)
    print('MP3 verificado: codec, duração e decodificação integral OK',flush=True)
