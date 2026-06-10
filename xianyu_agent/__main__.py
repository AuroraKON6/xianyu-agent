"""Run the xianyu agent server."""
import uvicorn
from .config import Settings

settings = Settings.from_env()

if __name__ == "__main__":
    uvicorn.run(
        "xianyu_agent.server:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=False,
    )
