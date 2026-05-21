"""python -m hrv_web"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("hrv_web.server:app", host="127.0.0.1", port=8765, reload=False)
