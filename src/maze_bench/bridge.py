from .transport import Transport,Proxy

class Broker:
    def __init__(self,key,cfg,case):
        self.transport=Transport(key,cfg['api_base'],cfg['source_alias'],case,cfg)
        self.proxy=Proxy(self.transport,cfg['harness'])if cfg.get('harness')else None
        self.base=self.proxy.base if self.proxy else None;self.calls=self.transport.calls;self.log=self.transport.log
    def set_feed(self,feed):self.transport.set_feed(feed)
    def close(self):
        if self.proxy:self.proxy.close()
