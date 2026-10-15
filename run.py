"""Inicie com python run.py; abra http://127.0.0.1:8000."""
import sys
from pathlib import Path
root=Path(__file__).resolve().parent
sys.path.insert(0,str(root))
if __name__=='__main__':
    import uvicorn
    uvicorn.run('api:app',host='127.0.0.1',port=8000)
