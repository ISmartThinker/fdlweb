import os
import time
import asyncio
import aiofiles
import logging
import socket
import mimetypes
from typing import Dict
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("tmpfiles")

STORE: Dict[str, dict] = {}
BASE_DIR = "/tmp"
MAX_FILE_SIZE = 20 * 1024 * 1024

try:
    os.makedirs(BASE_DIR, exist_ok=True)
except Exception as e:
    log.warning(f"Cannot create base dir: {e}")

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception:
        return "127.0.0.1"

async def cleaner():
    while True:
        try:
            await asyncio.sleep(30)
            now = time.time()
            dead = []
            for k, v in list(STORE.items()):
                if now > v["exp"]:
                    try:
                        file_path = Path(v["path"])
                        if file_path.exists():
                            file_path.unlink()
                    except Exception as e:
                        log.error(f"Error removing file: {e}")
                    dead.append(k)
            for k in dead:
                STORE.pop(k, None)
                log.info(f"Expired file removed: {k}")
        except Exception as e:
            log.error(f"Cleaner error: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    cleaner_task = asyncio.create_task(cleaner())
    yield
    cleaner_task.cancel()

app = FastAPI(lifespan=lifespan)

templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/v1/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    try:
        if not file.filename:
            raise HTTPException(status_code=400, detail="No filename")
        
        contents = await file.read()
        
        if len(contents) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="File too large. Max 20MB")
        
        original_name = file.filename.replace(" ", "_")
        safe_name = "".join(c for c in original_name if c.isalnum() or c in "._-")
        
        if not safe_name:
            safe_name = "file"
        
        fid = str(int(time.time() * 1000))
        
        file_path = os.path.join(BASE_DIR, f"{fid}_{safe_name}")
        
        async with aiofiles.open(file_path, 'wb') as f:
            await f.write(contents)
        
        exp = time.time() + 600
        
        STORE[fid] = {
            "path": file_path,
            "exp": exp,
            "filename": safe_name
        }
        
        file_url = f"{str(request.base_url).rstrip('/')}/dl/{fid}/{safe_name}"
        
        log.info(f"File uploaded: {file_path}")
        
        return JSONResponse({
            "status": "success",
            "data": {
                "url": file_url,
                "expires_in": 600
            }
        })
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Upload error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/dl/{fid}/{filename}")
async def get_file(fid: str, filename: str):
    try:
        if fid not in STORE:
            raise HTTPException(status_code=404, detail="File not found or expired")
        
        data = STORE.get(fid)
        
        if time.time() > data["exp"]:
            try:
                file_path = Path(data["path"])
                if file_path.exists():
                    file_path.unlink()
            except Exception as e:
                log.error(f"Error removing expired file: {e}")
            STORE.pop(fid, None)
            log.info(f"Expired on access: {fid}")
            raise HTTPException(status_code=404, detail="File expired")
        
        if not os.path.exists(data["path"]):
            STORE.pop(fid, None)
            raise HTTPException(status_code=404, detail="File not found")
        
        log.info(f"File served: {fid}")
        
        mimetype, _ = mimetypes.guess_type(filename)
        
        if mimetype and (mimetype.startswith('video/') or mimetype.startswith('audio/') or mimetype.startswith('image/')):
            return FileResponse(
                data["path"],
                media_type=mimetype,
                filename=filename
            )
        else:
            return FileResponse(
                data["path"],
                media_type=mimetype or "application/octet-stream",
                filename=filename
            )
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Download error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=404,
        content={"status": "error", "message": "File not found or expired"}
    )

@app.exception_handler(413)
async def too_large_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=413,
        content={"status": "error", "message": "File too large. Max 20MB"}
    )

@app.exception_handler(500)
async def internal_error_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": "Internal server error"}
    )

if __name__ == "__main__":
    import uvicorn
    
    host = "0.0.0.0"
    port = 3748
    local_ip = get_local_ip()
    
    log.info("=" * 60)
    log.info("TmpFiles API Server Starting")
    log.info("=" * 60)
    log.info(f"Local IP: http://{local_ip}:{port}")
    log.info(f"Network: http://0.0.0.0:{port} (all interfaces)")
    log.info(f"Localhost: http://127.0.0.1:{port}")
    log.info("=" * 60)
    
    uvicorn.run(
        "api:app",
        host=host,
        port=port,
        loop="uvloop",
        log_level="info",
        access_log=True
    )