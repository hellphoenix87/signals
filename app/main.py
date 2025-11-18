from fastapi import FastAPI
from contextlib import asynccontextmanager
import MetaTrader5 as mt5
from app.routes.endpoints import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not mt5.initialize():
        raise RuntimeError("MT5 initialization failed")
    yield
    mt5.shutdown()


app = FastAPI(lifespan=lifespan)
app.include_router(router)
