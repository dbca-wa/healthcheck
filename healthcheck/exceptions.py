
class SystemShutdown(Exception):
    def __init__(self,msg="System is shutdowning"):
        super().__init__(msg)

class MalformedData(Exception):
    pass

class FailedResponse(Exception):
    pass

class ConnectionClosed(Exception):
    pass
