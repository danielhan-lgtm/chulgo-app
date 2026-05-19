import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import config_router, boxhero, convert, slack_router, gmail_router, logs, receiving, doc_review, invoice

app = FastAPI(title="출고 라몬 API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(config_router.router, prefix="/api")
app.include_router(boxhero.router, prefix="/api/boxhero", tags=["boxhero"])
app.include_router(convert.router, prefix="/api/convert", tags=["convert"])
app.include_router(slack_router.router, prefix="/api/slack", tags=["slack"])
app.include_router(gmail_router.router, prefix="/api/gmail", tags=["gmail"])
app.include_router(logs.router, prefix="/api", tags=["logs"])
app.include_router(receiving.router, prefix="/api/receiving", tags=["receiving"])
app.include_router(doc_review.router, prefix="/api/doc-review", tags=["doc-review"])
app.include_router(invoice.router, prefix="/api/invoice", tags=["invoice"])


@app.get("/")
def root():
    return {"status": "ok", "app": "출고 라몬 API v2"}
