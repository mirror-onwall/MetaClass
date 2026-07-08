# Infrastructure

数据库、文件存储、LLM/TTS provider、任务执行器及第三方适配器。业务模块仅依赖这里定义的接口，不直接绑定具体厂商。

建议子目录：

- `database/`：SQLAlchemy engine、session 与 migration 配置。
- `storage/`：本地文件系统与未来对象存储适配器。
- `providers/`：LLM、TTS、PPT/PDF 渲染器及 fake provider。
- `tasks/`：单进程 worker，后续可替换为 RQ/Celery。
