def render_src_collection_route(target=None, passive_only=False):
    target_text = target or "待确认目标"

    if passive_only:
        return f"""
我理解你想先做被动信息收集，不发起主动扫描。

目标：{target_text}

建议路线：
1. 确认授权范围
   - 学校主域名
   - SRC 允许测试的系统范围
   - 是否允许第三方系统、统一认证、VPN、教务系统

2. 被动资产整理
   - 查询本地数据库已有子域名
   - 整理历史 subfinder / dnsx / httpx 结果
   - 归类门户、认证、教务、财务、就业、VPN、后台类资产

3. 目标优先级判断
   - 优先关注登录、统一认证、教务、财务、VPN、上传、查询类系统
   - 暂不进行漏洞验证
   - 暂不发起新扫描请求

4. 输出结果
   - 推荐关注目标
   - 数据来源
   - 保存位置
   - 可导出 CSV

你可以继续回复：
- 使用 peizheng.edu.cn 作为目标
- 查看已有结果
- 导出为 CSV
- 切换为主动收集
""".strip()

    return f"""
建议 SRC 信息收集路线：

目标：{target_text}

1. 明确授权范围
2. 被动信息收集
3. 子域名收集
4. 存活探测
5. 业务系统分类
6. 优先级排序
7. 在授权范围内进行合规测试

你可以选择：
- 只做被动信息收集
- 只看已有结果
- 生成主动扫描计划
- 取消
""".strip()
