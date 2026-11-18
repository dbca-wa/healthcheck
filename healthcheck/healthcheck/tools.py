import asyncio

from .healthcheck import HealthCheck
from .. import settings


async def clean_zombiefiles():
    hc = await HealthCheck.get_instance()
    await hc.clean_zombiefiles()
    edit_hc = await hc.get_editing_healthcheck()
    await edit_hc.clean_zombiefiles()



    
