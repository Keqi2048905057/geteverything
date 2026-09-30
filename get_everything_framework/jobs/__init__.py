"""异步任务执行器（M3）。

* :mod:`jobs.worker` —— 独立 worker 进程，``python -m jobs.worker``。
* :mod:`jobs.executor` —— 与进程无关的任务执行逻辑，便于单测直接调用。

刻意不放进 ``core/``，因为 ``core/`` 是纯数据与模型层，而这里是**进程**。
"""
