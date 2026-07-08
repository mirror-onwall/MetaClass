# Video

视频任务、口播稿、TTS、字幕和 ffmpeg 合成。

- `VideoJob`：保存 pending/running/finished/failed、进度、错误和 result_id。
- `VideoResult`：只保存成功生成的 MP4、字幕和时长。

当前 Job 在请求内同步执行，但任务与结果已分离，后续可迁移长任务 worker 而不改变契约。
