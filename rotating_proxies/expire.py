# -*- coding: utf-8 -*-
from __future__ import division
import time
import random
import logging
import math

import attr

from .utils import extract_proxy_hostport

logger = logging.getLogger(__name__)


class Proxies(object):
    """
    Expiring proxies container.

    代理过期容器

    过期机制说明::
    代理服务器有3种状态
    * 可用 good;
    * 不可用 dead;
    * 待检测 unchecked.

    爬虫初始载入代理列表时,所有代理被标识为 'unchecked'.

    当代理成功完成请求时,标记为 'good'.
    当使用代理请求失败时,标记为 'dead'.

    因此,只当代理处理于 'good' 和 'unchecked' 会被应用于请求,如果所有代理均处于 'dead' 状态,
    则，Spider需等候重新检测后有可用代理时才会重新启动
    

    被标记为 'Dead' 状态的代理，在一定的时间后将转为 'unchecked' 状态，即：复活 机制;  
    对每代理均被记录'Dead'状态的次数,  这个过程取决于 timeout 积累情况。
    timout
    """
    def __init__(self, proxy_list, backoff=None):
        self.proxies = {url: ProxyState() for url in proxy_list}
        self.proxies_by_hostport = {
            extract_proxy_hostport(proxy): proxy
            for proxy in self.proxies
        }
        self.unchecked = set(self.proxies.keys())
        self.good = set()
        self.dead = set()

        if backoff is None:
            backoff = exp_backoff_full_jitter
        self.backoff = backoff

    def get_random(self):
        """ Return a random available proxy (either good or unchecked) """
        available = list(self.unchecked | self.good)
        if not available:
            return None
        return random.choice(available)

    def get_proxy(self, proxy_address):
        """
        Return complete proxy name associated with a hostport of a given
        ``proxy_address``. If ``proxy_address`` is unkonwn or empty,
        return None.
        """
        if not proxy_address:
            return None
        hostport = extract_proxy_hostport(proxy_address)
        return self.proxies_by_hostport.get(hostport, None)

    def mark_dead(self, proxy, _time=None):
        """ Mark a proxy as dead """
        if proxy not in self.proxies:
            logger.warn("Proxy <%s> was not found in proxies list" % proxy)
            return

        if proxy in self.good:
            logger.debug("GOOD proxy became DEAD: <%s>" % proxy)
        else:
            logger.debug("Proxy <%s> is DEAD" % proxy)

        self.unchecked.discard(proxy)
        self.good.discard(proxy)
        self.dead.add(proxy)

        now = _time or time.time()
        state = self.proxies[proxy]
        state.backoff_time = self.backoff(state.failed_attempts)
        state.next_check = now + state.backoff_time
        state.failed_attempts += 1

    def mark_good(self, proxy):
        """ 标记当前代理状态 'Good' """
        if proxy not in self.proxies:
            logger.warn("Proxy <%s> was not found in proxies list" % proxy)
            return

        if proxy not in self.good:
            logger.debug("Proxy <%s> is GOOD" % proxy)

        self.unchecked.discard(proxy)
        self.dead.discard(proxy)
        self.good.add(proxy)
        self.proxies[proxy].failed_attempts = 0

    def reanimate(self, _time=None):
        """ 
        当'Dead' 状态的代理已休息过指定时间后,将 移至'unchecked'
        """
        n_reanimated = 0
        now = _time or time.time()
        for proxy in list(self.dead):
            state = self.proxies[proxy]
            assert state.next_check is not None
            if state.next_check <= now:
                self.dead.remove(proxy)
                self.unchecked.add(proxy)
                n_reanimated += 1
        return n_reanimated

    def reset(self):
        """ 重置所有 'Dead' 代理为 'unchecked' """
        for proxy in list(self.dead):
            self.dead.remove(proxy)
            self.unchecked.add(proxy)

    @property
    def mean_backoff_time(self):
        if not self.dead:
            return 0.0
        total_backoff = sum(self.proxies[p].backoff_time for p in self.dead)
        return float(total_backoff) / len(self.dead)

    @property
    def reanimated(self):
        return [p for p in self.unchecked if self.proxies[p].failed_attempts]

    def __str__(self):
        n_reanimated = len(self.reanimated)
        return "Proxies(good: {}, dead: {}, unchecked: {}, reanimated: {}, " \
               "mean backoff time: {}s)".format(
            len(self.good), len(self.dead),
            len(self.unchecked) - n_reanimated, n_reanimated,
            int(self.mean_backoff_time),
        )


@attr.s
class ProxyState(object):
    failed_attempts = attr.ib(default=0)
    next_check = attr.ib(default=None)
    backoff_time = attr.ib(default=None)  # for debugging


def exp_backoff(attempt, cap=3600, base=300):
    """ Exponential backoff time """
    # this is a numerically stable version of
    # min(cap, base * 2 ** attempt)
    max_attempts = math.log(cap / base, 2)
    if attempt <= max_attempts:
        return base * 2 ** attempt
    return cap


def exp_backoff_full_jitter(*args, **kwargs):
    """ Exponential backoff time with Full Jitter """
    return random.uniform(0, exp_backoff(*args, **kwargs))
