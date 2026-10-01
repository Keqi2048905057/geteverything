"""测试包的顶层 ``__init__.py``。

**这个文件不是装饰性的，删掉会让测试互相串味。**

``tests/`` 下有 ``unit/`` / ``integration/`` / ``fixtures/`` 三个子包，如果这里
没有 ``__init__.py``，``tests`` 对 Python 来说只是一个 namespace package（无普通包
的优先级），而本机的 ``site-packages`` 里恰好存在一个**常规包** ``tests``。
常规包优先于 namespace package，于是无论 ``sys.path`` 顺序如何，
``from tests.fixtures.local_http_server import ...`` 都会解析到 site-packages
里那个无关的包并报 ``ModuleNotFoundError: No module named 'tests.fixtures'``。

加上本文件后 ``tests`` 变成常规包并按 ``sys.path`` 顺序命中项目内的这一份，
测试才能用绝对导入引用夹具（见 ``tests/fixtures/local_http_server.py``）。
"""
