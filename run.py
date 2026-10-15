
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))

if __name__ == '__main__':
    import uvicorn

    uvicorn.run(
        'api:app',
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 8000))
    )
