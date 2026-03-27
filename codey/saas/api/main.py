"""Entry point for Codey API server."""
import uvicorn
from codey.saas.api.app import app

if __name__ == "__main__":
    uvicorn.run("codey.saas.api.main:app", host="0.0.0.0", port=8000, reload=True)
