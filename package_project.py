from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
root=Path(__file__).resolve().parent
with ZipFile(root.parent/'soundcloud-scraper.zip','w',ZIP_DEFLATED) as archive:
    for path in root.iterdir():
        if path.is_file() and path.suffix in {'.py','.html','.txt','.md','.json','.ps1'}:
            archive.write(path,'soundcloud-scraper/'+path.name)
    for path in (root/'downloads').iterdir():
        if path.is_file() and path.suffix in {'.mp3','.json'}:
            archive.write(path,'soundcloud-scraper/downloads/'+path.name)
print(root.parent/'soundcloud-scraper.zip')
