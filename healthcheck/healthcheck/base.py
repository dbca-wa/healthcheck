import inspect
import aiofiles
import hashlib
import traceback
import asyncio
import math
import json
import os
import logging
import httpx
import urllib.parse
#import urllib3
from datetime import datetime,timedelta,UTC
from zoneinfo import ZoneInfo
from collections import UserDict,OrderedDict

from .. import checks

from .healthcheckconfig import SectionHealthCheckConfig,SubrequestConfig,ServiceHealthCheckConfig,NOTEXIST
from .healthcheckpages import HealthCheckPages
from .. import settings
from .. import timezone
from .. import serializers
from .. import shutdown
from .. import utils

logger = logging.getLogger(__name__)

#urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
PRTGDATA_NOT_ENABLED = "Disabled"

class BaseServiceHealthCheckTask(object):
    def __init__(self,servicehealthcheck):
        self.servicehealthcheck = servicehealthcheck

    async def post_healthcheck(self,healthstatus):
        print(str(healthstatus))
        logger.debug("Health Check({0}.{1}) : {4}{6}  Running from {2} to {3}. {5}".format(
            healthstatus[0][0],
            healthstatus[0][1],
            healthstatus[1][1][0].strftime("%Y-%m-%d %H:%M:%S.%f"),
            healthstatus[1][1][1].strftime("%Y-%m-%d %H:%M:%S.%f"),
            healthstatus[1][1][2],
            healthstatus[1][1][3],
            "(Details Avaiable)" if healthstatus[1][1][4] else "(Detail Not Available)"
        ))
        pass

    async def run(self):
        attempts = 0
        endtime = None
        processtime = 0
        res = None
        healthstatus = None
        nextchecktime = self.servicehealthcheck.healthstatus_nextchecktime
        healthcheck = self.servicehealthcheck.healthcheck
        while not healthstatus:
            attempts += 1
            starttime = timezone.now()
            if self.servicehealthcheck.url:
                try:
                    try:
                        res = None
                        data = None
                        taskid = starttime.timestamp()
                        #logger.debug("{} : Start to run the healthcheck task({})".format(self.servicehealthcheck,self.__class__.__name__))
                        async with httpx.AsyncClient(auth=await self.servicehealthcheck.get_auth(self.servicehealthcheck,taskid),timeout=self.servicehealthcheck.request_timeout,verify=self.servicehealthcheck.sslverify,headers=await self.servicehealthcheck.get_requestheaders(self.servicehealthcheck,taskid),params=await self.servicehealthcheck.get_requestparameters(self.servicehealthcheck,taskid)) as client:
                            if self.servicehealthcheck.method == "GET":
                                res = await client.get(self.servicehealthcheck.url)
                            elif self.servicehealthcheck.method == "POST":
                                res = await client.post(self.servicehealthcheck.url,json=await self.servicehealthcheck.get_requestformdata(self.servicehealthcheck,taskid))
                            elif self.servicehealthcheck.method == "PUT":
                                res = await client.put(self.servicehealthcheck.url,json=await self.servicehealthcheck.get_requestformdata(self.servicehealthcheck,taskid))
                            elif self.servicehealthcheck.method == "DELETE":
                                res = await client.delete(self.servicehealthcheck.url)
                            else:
                                #Not support
                                raise Exception("Http method({}) Not Support".format(self.method))
                    finally:
                        endtime = timezone.now()
                        processtime = (endtime - starttime).total_seconds() * 1000
        
                    healthstatus = healthcheck.check_response(self.servicehealthcheck,res,attempts,processtime)
                except (httpx.ConnectTimeout,httpx.NetworkError,httpx.ProxyError) as ex:
                    if attempts >= self.servicehealthcheck.retry:
                        healthstatus = healthcheck.populate_healthstatus(self.servicehealthcheck,"red","httpx.{1}: {2}. Attempts={0}".format(attempts,ex.__class__.__name__,str(ex)),attempts,processtime)
                    else:
                        if self.servicehealthcheck.retry_interval:
                            await asyncio.sleep(self.servicehealthcheck.retry_interval)
                        continue
                except (httpx.TimeoutException,httpx.StreamError) as ex:
                    if attempts >= self.servicehealthcheck.retry:
                        healthstatus = healthcheck.populate_healthstatus(self.servicehealthcheck,"red","httpx.{1}: {2}. Attempts={0}".format(attempts,ex.__class__.__name__,str(ex)),attempts,processtime)
                    elif nextchecktime and (nextchecktime - endtime).total_seconds() >= self.servicehealthcheck.max_retry_processtime:
                        #still have time to try again
                        if self.servicehealthcheck.retry_interval:
                            await asyncio.sleep(self.servicehealthcheck.retry_interval)
                        continue
                    else:
                        healthstatus = healthcheck.populate_healthstatus(self.servicehealthcheck,"red","httpx.{1}: {2}. No time to try again. Attempts={0}".format(attempts,ex.__class__.__name__,str(ex)),attempts,processtime)
                except Exception as ex:
                    #traceback.print_exc()
                    if ex.__class__.__module__ == "builtins":
                        healthstatus = healthcheck.populate_healthstatus(self.servicehealthcheck,"error","{1}: {2}. Attempts={0}".format(attempts,ex.__class__.__name__,str(ex)),attempts,processtime)
                    else:
                        healthstatus = healthcheck.populate_healthstatus(self.servicehealthcheck,"error","{1}.{2}: {3}. Attempts={0}".format(attempts,ex.__class__.__module__,ex.__class__.__name__,str(ex)),attempts,processtime)
            else:
                healthstatus = ["green","OK",None]
                endtime = timezone.now()

        healthstatus.insert(0,starttime)
        healthstatus.insert(1,endtime)
        
        healthstatus.append(healthstatus[2] in self.servicehealthcheck.healthdetailpersistent)
        self.servicehealthcheck.healthstatus_healthdata = healthstatus
        self.servicehealthcheck.healthstatus_running = False

        try:
            await self.servicehealthcheck.save_checkingstatus(healthstatus,res,attempts)
        except Exception as ex:
            #traceback.print_exc()
            logger.error("Failed to save the healthcheck status details({2}) of service({0}.{1}). {3}: {4}".format(self.servicehealthcheck.sectionid,self.servicehealthcheck.id,healthstatus,ex.__class__.__name__,str(ex)))

        try:
            if inspect.iscoroutinefunction(self.post_healthcheck):
                await self.post_healthcheck([[self.servicehealthcheck.sectionid,self.servicehealthcheck.id],[self.servicehealthcheck.healthstatus_nextchecktime,healthstatus]])
            else:
                self.post_healthcheck([[self.servicehealthcheck.sectionid,self.servicehealthcheck.id],[self.servicehealthcheck.healthstatus_nextchecktime,healthstatus]])
        except Exception as ex:
            logger.error("Failed to call 'post_healthcheck'({2}) of service({0}.{1}). {3}: {4}".format(self.servicehealthcheck.sectionid,self.servicehealthcheck.id,healthstatus,ex.__class__.__name__,str(ex)))


class JsonStatusMixin(object):
    def get_jsonstatus(self,details=False):
        now = timezone.now()
        data = {}
        for section in self.healthchecksections:
            sectiondata = {}
            data[section.id] = sectiondata
            for service in section.healthcheckservices:
                if details:
                    if service.healthstatus_nextchecktime + timedelta(milliseconds=service["timeout"]) < now:
                        sectiondata[service.id] = {
                            'status': "error",
                            'starttime': "",
                            'endtime': "",
                            'message': "The service should be checked at '{}', but it didn't".format(service.healthstatus_nextcheck),
                            'nextcheck':""
                        }
                    else:
                        sectiondata[service.id] = {
                            'status': service.healthstatus_name,
                            'starttime': service.healthstatus_checkstart,
                            'endtime': service.healthstatus_checkend,
                            'message':service.healthstatus_info,
                            'nextcheck':service.healthstatus_nextcheck
                        }
                else:
                    if service.healthstatus_nextchecktime + timedelta(milliseconds=service["timeout"]) < now:
                        #the current healthstatus is outdated
                        sectiondata[service.id] = "error"
                    else:
                        sectiondata[service.id] = service.healthstatus_name
        return data

class PRTGMixin(object):
    def get_prtgdata(self,details=False):
        data = {"error":0,"result":[],"text":"All checks passed"}
        failed_services = []
        warning_services = []
        now = timezone.now()
        servicecritical = {}
        for section in self.healthchecksections:
            if not section.enabled:
                continue
            if not section.prtgenabled:
                continue
            for service in section.healthcheckservices:
                if not service.enabled:
                    continue
                if not service.url:
                    continue
                if not service.prtgenabled:
                    continue

                if not service.healthstatus_nextchecktime or service.healthstatus_nextchecktime + timedelta(milliseconds=service["timeout"]) < now:
                    #the current healthstatus is outdated
                    prtgdata = None
                    healthstatus_name = "error"
                else:
                    prtgdata = service.healthstatus_prtgdata
                    healthstatus_name = service.healthstatus_name

                if prtgdata == PRTGDATA_NOT_ENABLED:
                    continue

                if service.prtg :
                    for channelid,prtgconfig in service.prtgchannels:
                        prtgchannel,getdata_map,computed_columns = prtgconfig
                        prtgchannel = dict(prtgchannel)
                        if prtgdata is not None and prtgdata.get(channelid) is not None:
                            prtgchannel["value"] = prtgdata[channelid]

                        for k,v in computed_columns.items():
                            prtgchannel[k] = v(prtgchannel["value"])


                        data["result"].append(prtgchannel)

                if healthstatus_name in ("red","error"):
                    if service.criticalweight:
                        servicecritical[service.criticalweight[0]] = servicecritical.get(service.criticalweight[0],0) + service.criticalweight[1]
                    failed_services.append(service.name)
                elif healthstatus_name  == "yellow":
                    warning_services.append(service.name)

        if any( (v >= 1) for v in servicecritical.values()):
            data["error"] = 1

        if failed_services or warning_services:
            if failed_services and warning_services:
                data["text"] = "The services({}) are not available; The services({}) aren't healthy.".format(failed_services,warning_services)
            elif failed_services:
                data["text"] = "The services({}) are not available.".format(failed_services)
            else:
                data["text"] = "The services({}) aren't healthy.".format(warning_services)

        return {"prtg":data}

class Subrequest(SubrequestConfig):
    def __init__(self,healthcheck,section,configindex,data):
        super().__init__(healthcheck,section,configindex,data)
        self._values = {}
        self._expireat = None
        self._service = None
        self._taskid = None

    async def get_value(self,service,taskid,prop):
        starttime = timezone.now()
        if not self._values  or ((self._expireat is None or starttime >= self._expireat) and (self._service is not  service or self._taskid != taskid)):
            self._values.clear()
            self._expireat = None
            self._service = None
            self._taskid = None

            try:
                res = None
                data = None
                #logger.debug("{} : Send the subrequest({}) to get the value of property({})".format(self.id,self.url,prop))
                async with httpx.AsyncClient(auth=await self.get_auth(service,taskid),timeout=self.request_timeout,verify=self.sslverify,headers=await self.get_requestheaders(service,taskid),params=await self.get_requestparameters(service,taskid)) as client:
                    if self.method == "GET":
                        res = await client.get(self.url)
                    elif self.method == "POST":
                        res = await client.post(self.url,data=await self.get_requestformdata(service,taskid))
                    elif self.method == "PUT":
                        res = await client.put(self.url,data=await self.get_requestformdata(service,taskid))
                    elif self.method == "DELETE":
                        res = await client.delete(self.url)
                    else:
                        #Not support
                        raise Exception("Http method({}) Not Support".format(self.method))

                #populate the data from response
                found = False
                for checkconditions,valuesmap,transforms in self["values"]:
                    #transform the response
                    #transformed response should be compatible with the original response
                    if transforms:
                        for transform in transforms:
                            res = transform(res)

                    checkresult =  checks.check(res,checkconditions)
                    if not checkresult:
                        continue
                    for k,f in valuesmap.items():
                        self._values[k] = f(res)
                    found = True

                if not found:
                    raise Exception("Faild to find the matched condition for the response({}:{})".format(res.status_code,res.text))
                
                if self._values.get("expireat"):
                    if isinstance(self._values["expireat"],datetime):
                        self._expireat = self._values["expireat"] - self.expirebuffer
                    else:
                        self._expireat = timezone.parse(self._values["expireat"],pattern=self._values.get("expireat_pattern"),timezone=self._values.get("expireat_tz")) - self.expirebuffer
                elif self._values.get("expireat_timestamp"):
                    self._expireat = timezone.fromtimestamp(float(self._values["expireat_timestamp"]),tz=self._values.get("expireat_tz")) - self.expirebuffer
                elif self._values.get("expire"):
                    if self._values.get("expire_unit"):
                        if self._values["expire_unit"] == "s":
                            self._expireat = starttime + timedelta(seconds=float(self._values["expire"])) - self.expirebuffer
                        elif self._values["expire_unit"] == "ms":
                            self._expireat = starttime + timedelta(milliseconds=float(self._values["expire"])) - self.expirebuffer
                        elif self._values["expire_unit"] == "m":
                            self._expireat = starttime + timedelta(minutes=float(self._values["expire"])) - self.expirebuffer
                        elif self._values["expire_unit"] == "h":
                            self._expireat = starttime + timedelta(hours=float(self._values["expire"])) - self.expirebuffer
                        elif self._values["expire_unit"] == "d":
                            self._expireat = starttime + timedelta(days=float(self._values["expire"])) - self.expirebuffer
                        else:
                            raise Exception("Timeunit({}) Not Support".format(self._values["expire_unit"]))
                    else:
                        self._expireat = starttime + timedelta(seconds=int(self._values["expire"])) - self.expirebuffer
                else:
                    self._expireat = None

                self._service = service
                self._taskid = taskid
                
            except Exception as ex:
                raise Exception("Failed to populate the data from response.{}({})".format(ex.__class__.__name__,str(ex)))
        else:
            #logger.debug("{} : Reuse the value of property({}) fetched from the previous request".format(self.url,prop))
            pass

        return self._values.get(prop)

class ServiceHealthCheck(ServiceHealthCheckConfig):
    def __init__(self,healthcheck,section,configindex,data):
        super().__init__(healthcheck,section,configindex,data)
        self.healthcheckpages = HealthCheckPages.get_instance(self)

    _basedir = None
    @property
    def basedir(self):
        if not self._basedir:
            self._basedir = os.path.join(self.section.basedir,self.id)

        return self._basedir

    _last_greenhealthcheck = NOTEXIST
    async def load_last_greenhealthcheck(self):
        if self._last_greenhealthcheck is NOTEXIST: 
            self._last_greenhealthcheck = await self.healthcheckpages.get_last_greenhealthcheck()

    @property
    def last_greenhealthcheck(self):
        return self._last_greenhealthcheck

    _last_yellowhealthcheck = NOTEXIST
    async def load_last_yellowhealthcheck(self):
        if self._last_yellowhealthcheck is NOTEXIST: 
            self._last_yellowhealthcheck = await self.healthcheckpages.get_last_yellowhealthcheck()

    @property
    def last_yellowhealthcheck(self):
        return self._last_yellowhealthcheck

    _last_greenyellowhealthcheck = NOTEXIST
    async def load_last_greenyellowhealthcheck(self):
        if self._last_greenyellowhealthcheck is NOTEXIST: 
            self._last_greenyellowhealthcheck = await self.healthcheckpages.get_last_greenyellowhealthcheck()

    @property
    def last_greenyellowhealthcheck(self):
        return self._last_greenyellowhealthcheck

    _last_redhealthcheck = NOTEXIST
    async def load_last_redhealthcheck(self):
        if self._last_redhealthcheck is NOTEXIST: 
            self._last_redhealthcheck = await self.healthcheckpages.get_last_redhealthcheck()

    @property
    def last_redhealthcheck(self):
        return self._last_redhealthcheck

    _last_errorhealthcheck = NOTEXIST
    async def load_last_errorhealthcheck(self):
        if self._last_errorhealthcheck is NOTEXIST: 
            self._last_errorhealthcheck = await self.healthcheckpages.get_last_errorhealthcheck()

    def last_errorhealthcheck(self):
        return self._last_errorhealthcheck

    @property
    def healthstatus(self):
        """
        server side: [next checktime,[starttime,endtime,health status,health status message,prtg data,health checking persistent?],running?] 
        app side: [next checktime,[starttime,endtime,health status,health status message,prtg data,health checking persistent?]] 
        """
        return self.get("healthstatus")

    @healthstatus.setter
    def healthstatus(self,val):
        self["healthstatus"] = val

    @property
    def healthstatus_running(self):
        """
        Only available in server side
        """
        status = self.get("healthstatus")
        return status[2] if status else False

    @healthstatus_running.setter
    def healthstatus_running(self,val):
        """
        Only available in server side
        """
        self["healthstatus"][2] = val

    @property
    def healthstatus_healthdata(self):
        status = self.get("healthstatus")
        return status[1] if status else None

    @healthstatus_healthdata.setter
    def healthstatus_healthdata(self,val):
        if val[2] == "green":
            self._last_greenhealthcheck = val
            self._last_greenyellowhealthcheck = val
        elif val[2] == "yellow":
            self._last_yellowhealthcheck = val
            self._last_greenyellowhealthcheck = val
        elif val[2] == "red":
            self._last_redhealthcheck = val
        else:
            self._last_errorhealthcheck = val
        self["healthstatus"][1] = val

    @property
    def healthstatus_nextchecktime(self):
        status = self.get("healthstatus")
        return status[0] if status else None

    @healthstatus_nextchecktime.setter
    def healthstatus_nextchecktime(self,val):
        self["healthstatus"][0] = val

    @property
    def healthstatus_nextcheck(self):
        status = self.get("healthstatus")
        return status[0].strftime("%Y-%m-%dT%H:%M:%S") if status else ""

    @property
    def healthstatus_name(self):
        status = self.get("healthstatus")
        return status[1][2] if status and status[1] else ""

    @property
    def healthstatus_info(self):
        status = self.get("healthstatus")
        return status[1][3] if status and status[1] else ""

    @property
    def healthstatus_prtgdata(self):
        status = self.get("healthstatus")
        return status[1][4] if status and status[1] and len(status[1]) >= 6 else None

    @property
    def healthstatus_persistent(self):
        status = self.get("healthstatus")
        return status[1][-1] if status and status[1] else False

    @property
    def healthstatus_checkstart(self):
        status = self.get("healthstatus")
        return status[1][0].strftime("%Y-%m-%dT%H:%M:%S.%f") if status and status[1] else ""

    @property
    def healthstatus_checkend(self):
        status = self.get("healthstatus")
        return status[1][1].strftime("%Y-%m-%dT%H:%M:%S.%f") if status and status[1] else ""

    async def clean_zombiefiles(self):
        await self.healthcheckpages.clean_zombiefiles()

    def get_nextchecktime(self,last_checkingtime,now=None,today=None,tomorrow=None,seconds_in_day=None):
        offset = self.offset
        if not now:
            now = timezone.now()
            today = datetime(year = now.year,month=now.month,day=now.day,tzinfo=settings.TZ)
            tomorrow = today + timedelta(days=1)
            seconds_in_day = int((now - today).total_seconds())

        nextchecktimeseconds_without_offset = seconds_in_day - (seconds_in_day % self.interval)
        nextchecktime = today + timedelta(seconds=nextchecktimeseconds_without_offset + offset)

        if last_checkingtime and nextchecktime <= last_checkingtime:
            addedseconds = self.interval * math.ceil(((last_checkingtime - nextchecktime).total_seconds() + 1)/self.interval)
            nextchecktimeseconds_without_offset += addedseconds
            nextchecktime_without_offset = today + timedelta(seconds=nextchecktimeseconds_without_offset)

            nextchecktime += timedelta(seconds=addedseconds)
            if nextchecktime_without_offset >= tomorrow:
                nextchecktime_without_offset = tomorrow
                nextchecktimeseconds_without_offset = 0
                nextchecktime = nextchecktime_without_offset + timedelta(seconds=offset)


        checkingtime = self["checkingtime"]
        if not checkingtime:
            return nextchecktime

        index = -1
        for i in range(len(checkingtime)):
            starttime = checkingtime[i][0]
            endtime = checkingtime[i][1]
            if nextchecktimeseconds_without_offset >= starttime and nextchecktimeseconds_without_offset < endtime:
                return nextchecktime
            elif nextchecktimeseconds_without_offset < starttime:
                if starttime % self.interval == 0:
                    return today + timedelta(seconds=starttime + offset)
                else:
                    return today + timedelta(seconds=starttime + self.interval - (starttime % self.interval) + offset)

        #can't find the next check time in the same day, try next day
        if starttime % self.interval == 0:
            return tomorrow + timedelta(seconds=checkingtime[0][0] + offset)
        else:
            return tomorrow + timedelta(seconds=checkingtime[0][0] + self.interval - (checkingtime[0][0] % self.interval) + offset)

    async def save_checkingstatus(self,healthstatus,res,attempts):
        if healthstatus[-1]:
            details = {
                "request": {
                    "url": self.url,
                    "method": self.method,
                    "sslverify" :self.sslverify
                },
                "healthstatus":healthstatus,
            }
            if attempts > 1:
                details["request"]["attempts"] = attempts
            if self.fixedheaders:
                details["request"]["headers"] = self.fixedheaders
    
            if self.timeout:
                details["request"]["timeout"] = self.timeout
    
            if self.method in ("POST","PUT"):
                details["request"]["data"] = self.fixedformdata
    
            if res:
                try:
                    content_type = res.headers.get("Content-Type")
                    if "json" in content_type:
                        body = res.json()
                    elif any(key in content_type for key in ("text","html","xml")):
                        body = res.text
                    else:
                        body = "Non text repsonse"
                except Exception as ex:
                    body = "Failed to get body.{}: {}".format(ex.__class__.__name__,str(ex))
                details["response"] = {
                    "status_code" :  res.status_code,
                    "headers": [],
                    "body": body
                }
                for name,val in res.headers.items():
                    if name.lower() in ("connection","server","x-frame-options","x-content-type-options","access-control-allow-methods","content-encoding","accept-ranges","date","via","vary","transfer-encoding"):
                        continue
                    details["response"]["headers"].append([name,val])
        else:
            details = None

        await self.healthcheckpages.save(healthstatus,details)

    async def load_checkinghistory(self):
        last_healthcheck = await self.healthcheckpages.get_last_healthcheck()

        #load dependent async properties
        for prop in self.dependent_async_properties:
            await getattr(self,"load_{}".format(prop))()

        next_checktime = self.get_nextchecktime(last_healthcheck[0] if last_healthcheck else None)
        self.healthstatus = [next_checktime,last_healthcheck,False] 

class SectionHealthCheck(SectionHealthCheckConfig):
    SERVICEHEALTHCHECK_CLS = ServiceHealthCheck
    SUBREQUEST_CLS = Subrequest


    _basedir = None
    @property
    def basedir(self):
        if not self._basedir:
            self._basedir = os.path.join(self.healthcheck.basedir,self.id)

        return self._basedir

    async def clean_zombiefiles(self):
        """
        Clean the data files which are expired or not generated by healthcheck
        """
        servicefolders = set()
        for service in self["services"].values():
            servicefolders.add(service.basedir)
            await service.clean_zombiefiles()

        for f in await aiofiles.os.listdir(self.basedir):
            path = os.path.join(self.basedir,f)
            if path not in servicefolders:
                #this path is not for any section
                if await aiofiles.os.path.isfile(path):
                    logger.info("{}: Delete the file({}) which is not belonging to healthcheck".format(self,path))
                    await utils.removefile(path)
                else:
                    logger.info("{}: Delete the folder({}) which is not belonging to healthcheck".format(self,path))
                    await utils.deletedir(path)

class BaseHealthCheck(PRTGMixin,JsonStatusMixin):
    configfile = None
    _checkingstatus_loaded = False
    def __init__(self,configfile=settings.HEALTHCHECK_CONFIGFILE):
        """
        configs : a json file or list object
        """
        self.config_hashcode = None
        self.sections = None
        self.configfile = configfile
        self._name = "{}({})".format(self.__class__.__name__,os.path.basename(self.configfile))
        self._continuous_check_task = None

    def __str__(self):
        return self._name

    @property
    def title(self):
        return "DBCA Essential Systems Health Check"

    @property
    def healthchecksections(self):
        return filter(lambda section: section.enabledservices > 0,self.sections.values()) if self.sections else []

    def get_service(self,section,service):
        return self.sections.get(section,{}).get("services",{}).get(service)

    def get_subrequest(self,section,request):
        """
        Get the request object if found;otherwise return None
        """
        if self.sections:
            return self.sections.get(section,{}).get("subrequests",{}).get(request)
        else:
            return None

    _basedir = None
    @property
    def basedir(self):
        if not self._basedir:
            self._basedir = os.path.join(settings.HEALTHCHECK_DATA_DIR,os.path.splitext(os.path.basename(self.configfile))[0])

        return self._basedir

    async def clean_zombiefiles(self):
        """
        Clean the data files which are expired or not generated by healthcheck
        """
        sectionfolders = set()
        for section in self.sections.values():
            sectionfolders.add(section.basedir)
            await section.clean_zombiefiles()

        for f in await aiofiles.os.listdir(self.basedir):
            path = os.path.join(self.basedir,f)
            if path not in sectionfolders:
                #this path is not for any section
                if await aiofiles.os.path.isfile(path):
                    logger.info("{}: Delete the file({}) which is not belonging to healthcheck".format(self,path))
                    await utils.removefile(path)
                else:
                    logger.info("{}: Delete the folder({}) which is not belonging to healthcheck".format(self,path))
                    await utils.deletedir(path)


    async def reload(self):
        """
        Reload the configuration 
        """
        #keep the current healthcheck objects
        sections = self.sections
        #recreate the healthcheck objects from healthcheck config json file
        changed = await self.load_configs()
        if not changed:
            return False

        #migrate the current healthcheck status from previous healthcheck objects to new healtcheck objects
        now = timezone.now()
        today = datetime(year = now.year,month=now.month,day=now.day,tzinfo=settings.TZ)
        tomorrow = today + timedelta(days=1)
        seconds_in_day = int((now - today).total_seconds())
        for section in self.healthchecksections:
            for service in section.healthcheckservices:
                existing_service = sections.get(section.id,{}).get("services",{}).get(service.id)
                next_checktime = today + timedelta(seconds=seconds_in_day - (seconds_in_day % service.interval))
                if existing_service:
                    if existing_service.healthstatus:
                        service.healthstatus = existing_service.healthstatus
                        if service.healthstatus[0] < next_checktime:
                            service.healthstatus[0] = next_checktime
                        else:
                            next_checktime += timedelta(seconds=service.interval)
                            if next_checktime  >= tomorrow:
                                service.healthstatus[0] = tomorrow
                            else:
                                service.healthstatus[0] = next_checktime

                    service._last_greenhealthcheck = existing_service._last_greenhealthcheck
                    service._last_yellowhealthcheck = existing_service._last_yellowhealthcheck
                    service._last_greenyellowhealthcheck = existing_service._last_greenyellowhealthcheck
                    service._last_redhealthcheck = existing_service._last_redhealthcheck
                    service._last_errorhealthcheck = existing_service._last_errorhealthcheck

                else:
                    await service.load_checkinghistory()

        #remove the data files for removed sections and services
        for existing_section in sections.values():
            if existing_section.id not in self.sections:
                #removed 
                asyncio.create_task(utils.deletedir(existing_section.basedir))
                continue
            for existing_service in existing_section.get("services",{}).values():
                if existing_service.id not in self.sections[existing_section.id]["services"]:
                    #removed
                    asyncio.create_task(utils.deletedir(existing_service.basedir))

        return True


    async def load_configs(self,force=False):
        """
        Return True if loaded; False if not changed.

        """
        if not self.configfile:
            raise Exception("The configuration is not loaded from a json file, no need to load")

        try:
            logger.debug("Begin to load health check configurations({})".format(self.configfile))
            async with aiofiles.open(self.configfile,'rb') as f:
                configs = await f.read()
            if not configs:
                async with aiofiles.open(self.configfile,'w') as f:
                    await f.write("[]")
                configs = b"[]"

        except FileNotFoundError as ex:
            async with aiofiles.open(self.configfile,'w') as f:
                await f.write("[]")
            configs = b"[]"

        try:
            config_hashcode = hashlib.sha256(configs).hexdigest()
            if not force and config_hashcode == self.config_hashcode:
                return False

            self.config_hashcode = config_hashcode
            self.init_configs(json.loads(configs.decode()))
            return True
        except Exception as ex:
            traceback.print_exc()
            raise Exception("The config file({}) is an invalid jason file. {} : {}".format(self.configfile,ex.__class__.__name__,str(ex)))

    async def load_checkingstatus(self):
        logger.debug("Begin to load health checking status")
        for section in self.sections.values():
            for service in section["services"].values():
                await service.load_checkinghistory()
        self._checkingstatus_loaded = True


    def init_configs(self,configs):
        #initialize the configs
        now = timezone.now()
        today = datetime(year = now.year,month=now.month,day=now.day,tzinfo=settings.TZ)
        tomorrow = today + timedelta(days=1)
        seconds_in_day = int((now - today).total_seconds())
        if configs is None:
            configs = []
            
        if not isinstance(configs,(list,tuple)):
            raise Exception("Healthcheck configurations should be a list object.{}".format(configs))

        self.sections = OrderedDict()
        #add a fake service for healthcheck heartbeat
        heartbeat = SectionHealthCheck(self,-1,{
            "id": "Healthcheck",
            "name": "",
            "interval":settings.HEARTBEAT,
            "healthdetailpersistent":[],
            "historyexpire":0,
            "enabled":True,
            "errorhistoryexpire":0,
            "services":[{
                "id":"Healthcheck-Heartbeat",
                "name":"Healthcheck Heartbeat",
                "interval":settings.HEARTBEAT,
                "location":"",
                "healthdetailpersistent":[],
                "historyexpire":0,
                "errorhistoryexpire":0,
                "timeout":100,
                "offset":0,
                "prtg":None,
                "enabled":True,
                "checkingtime":None
            }]
        })
       
        sectionindex = 0
        for config in configs:
            sectionindex += 1
            SectionHealthCheck(self,sectionindex,config)
            
    def _schedule_continuous_check(self,taskcls,*args):
        shutdown.unregister_scheduled_task(self._continuous_check_task)
        self._continuous_check_task = asyncio.create_task(self._continuous_check(taskcls,*args))

    def stop_continuous_check(self):
        if not self._continuous_check_task:
            logger.debug("{}: The continuous health check has already been stopped.".format(self))
            return

        shutdown.unregister_scheduled_task(self._continuous_check_task)
        self._continuous_check_task.cancel()
        self._continuous_check_task = None
        logger.debug("{}: Stop continuous health check".format(self))

    async def _continuous_check(self,taskcls,*args):
        if shutdown.shutdowning:
            logger.info("The continuous health checking is end.")
            self._continuous_check_task = None
            return
        
        if not self._continuous_check_task:
            #already stopped
            return

        now = timezone.now()
        today = datetime(year = now.year,month=now.month,day=now.day,tzinfo=settings.TZ)
        tomorrow = today + timedelta(days=1)
        seconds_in_day = int((now - today).total_seconds())

        self._next_runtime = None
        for section in self.sections.values():
            if not section.enabled:
                continue
            for service in section["services"].values():
                if not service.enabled:
                    continue
                if now >= service.healthstatus_nextchecktime:
                    #check this service now
                    if service.healthstatus_running:
                        #the healthcheck of this service is running now
                        #can't launch another healthcheck. delay 5 seconds and try again
                        next_checktime = now + timedelta(seconds=5)
                    else:
                        #the healthcheck of this service is not running
                        next_checktime = service.get_nextchecktime(service.healthstatus_nextchecktime,now,today,tomorrow,seconds_in_day)
                        logger.debug("{} : Run a task to check the service({}.{}.lastchecktime = {}, next checktime={})  to task runner.".format(self,service.sectionid,service.id,service.healthstatus_nextcheck,next_checktime))
                        service.healthstatus_running = True
                        if service.offset:
                            service["interval_start"] = service.healthstatus_nextchecktime - timedelta(seconds=service.interval + service.offset)
                            service["interval_end"] = service.healthstatus_nextchecktime - timedelta(seconds=service.offset)
                        else:
                            service["interval_start"] = service.healthstatus_nextchecktime - timedelta(seconds=service.interval)
                            service["interval_end"] = service.healthstatus_nextchecktime
                        task = taskcls(service,*args)
                        #set the nextchecktime
                        service.healthstatus_nextchecktime = next_checktime
                        #create a task to run the healthcheck
                        asyncio.create_task(task.run())
                else:
                    next_checktime = service.healthstatus_nextchecktime
                if not self._next_runtime or self._next_runtime > next_checktime:
                    self._next_runtime = next_checktime

        if not self._continuous_check_task:
            #already stopped
            return

        if not self._next_runtime:
            self._next_runtime = now + timedelta(seconds=30)

        seconds = (self._next_runtime - timezone.now()).total_seconds()
        if seconds > 0:
            logger.debug("Waiting {} seconds to begin the next batch of service health check.".format(seconds))
            self._continuous_check_task = asyncio.get_running_loop().call_later(seconds,self._schedule_continuous_check,taskcls,*args)
            shutdown.register_scheduled_task(self._continuous_check_task)
        else:
            self._continuous_check_task = asyncio.create_task(self._continuous_check(taskcls,*args))

    @property
    def is_continuous_check_started(self):
        return self._continuous_check_task != None

    _continuous_check_task = None
    async def continuous_check(self,*args,taskcls=BaseServiceHealthCheckTask):
        if not self._checkingstatus_loaded:
            await self.load_checkingstatus()
        if self._continuous_check_task:
            logger.info("{}: The continuous health checking is already started.".format(self))
            return 
        logger.info("{}: Start to run the continuous health checking".format(self))
        self._continuous_check_task = asyncio.create_task(self._continuous_check(taskcls,*args))


    @classmethod
    def populate_healthstatus(cls,serviceconfig,status,message,attempts,processtime):
        """
        Get the healthstatus for exception
        """
        if serviceconfig["prtg"]:
            prtgdata = {}
            for channelid in serviceconfig["prtg"].keys():
                if channelid == "__status__":
                    prtgdata[channelid] = 3 if status == "green" else (2 if status == "yellow" else (1 if status == "red" else 0))
                elif channelid == "__processtime__":
                    prtgdata[channelid] = int(processtime) #milliseconds
                elif channelid == "__attempts__":
                    prtgdata[channelid] = attempts
        else:
            prtgdata = PRTGDATA_NOT_ENABLED

        return [status,message,prtgdata]

    @classmethod
    def check_response(cls,serviceconfig,res,attempts,processtime):
        """
        Return [traffic light, msgs,prtg data]
        """
        healthstatus = None
        messages = [] if settings.HEALTHCHECK_CONDITION_VERBOSE else None
        for key in ("green","yellow","red","error"):
            if key not in serviceconfig["healthchecks"]:
                continue
            checkconditions,get_checkmessage,get_prtgdata,transforms = serviceconfig["healthchecks"][key]
            try:
                if settings.HEALTHCHECK_CONDITION_VERBOSE:
                    messages.clear()
                #transform the response
                #transformed response should be compatible with the original response
                if transforms:
                    for transform in transforms:
                        res = transform(res)

                checkresult =  checks.check(res,checkconditions,messages=messages)
                if checkresult:
                    checkmsg = get_checkmessage(res)
                    if serviceconfig["prtg"]:
                        prtgdata = {}
                        for channelid in serviceconfig["prtg"].keys():
                            if get_prtgdata.get(channelid):
                                prtgdata[channelid] = get_prtgdata[channelid](res)
                            elif channelid == "__status__":
                                prtgdata[channelid] = 3 if key == "green" else (2 if key == "yellow" else (1 if key == "red" else 0))
                            elif channelid == "__processtime__":
                                prtgdata[channelid] = int(processtime) #milliseconds
                            elif channelid == "__attempts__":
                                prtgdata[channelid] = attempts
                    else:
                        prtgdata = PRTGDATA_NOT_ENABLED

                    if not isinstance(checkmsg,str):
                        checkmsg = json.dumps(checkmsg,indent=4,cls=serializers.JSONFormater)
                    if settings.DEBUG and messages:
                        msg = "{}\nThe following conditions are checked.\n    {}".format(checkmsg,"\n    ".join(messages))
                    else:
                        msg = checkmsg
                    healthstatus = [key,msg,prtgdata]
                    break
                else:
                    """
                    if settings.DEBUG:
                        msg = "The healthstatus({}) are not satified.\n    {}\n    The following conditions are checked.\n        {}".format(key,get_checkmessage(res),"\n        ".join(messages))
                    else:
                        msg = "The healthstatus({}) are not satified.\n    {}".format(key,get_checkmessage(res))
                    logger.debug(msg)
                    """
                    pass

            except Exception as ex:
                traceback.print_exc()
                healthstatus = cls.populate_healthstatus(serviceconfig,"error","Failed to check health status '{}'.{}:{}".format(key,ex.__class__.__name__,str(ex)),attempts,processtime)
                break
    
        if not healthstatus:
            if res:
                contenttype = res.headers.get("Content-Type","").lower()
                if any( (key in contenttype) for key in ("text","json","xml")):
                    message = res.text
                else:
                    message = "non-text response"
                healthstatus = cls.populate_healthstatus(serviceconfig,"error","Status Code:{}, Message:{}".format(res.status_code,message),attempts,processtime)
            else:
                healthstatus = cls.populate_healthstatus(serviceconfig,"error","All healthstatus configured in {} are not satisfied.",attempts,processtime)


        if attempts > 1:
            healthstatus[1] = "{1}. Attempts:{0}".format(attempts,healthstatus[1])
    
        return healthstatus



