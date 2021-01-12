# -*- coding: utf-8 -*-
from scrapy.exceptions import IgnoreRequest

class BanDetectionPolicy(object):
    """ 
    检测的请求是否被墙。

    默认检测的请求被墙的状态码和Request异常::

    Http状态码：
        200，301，302

    Request异常:
        IgnoreRequest
        TODO::
        IndexError 待添加
    """
    NOT_BAN_STATUSES = {200, 301, 302}
    NOT_BAN_EXCEPTIONS = (IgnoreRequest,)

    def response_is_ban(self, request, response):
        # 如果为非 200,301,302,则,视为是被墙了
        if response.status not in self.NOT_BAN_STATUSES:
            return True
        # 如果 状态码为200，但，respones返回的内容为空,也视为是被墙了
        if response.status == 200 and not len(response.body):
            return True
        return False

    def exception_is_ban(self, request, exception):
        return not isinstance(exception, self.NOT_BAN_EXCEPTIONS)
