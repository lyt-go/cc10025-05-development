"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers.label_batch import router as label_batch_router
from app.routers.resource import router as resource_router

ROUTERS = [label_batch_router, resource_router]
