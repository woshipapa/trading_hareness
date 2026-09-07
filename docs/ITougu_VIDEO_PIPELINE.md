# 爱投顾视频归档与转写

当前接入分为两层：`itougu_neican_relay.py` 只读取并转发
`videoInfo.videoUrl`；`scripts/itougu_video_worker.py` 在具备存储和媒体依赖的
owner 节点上做受限 HLS 探测和音频抽取。两者均不把视频内容或转写结果写入实时
策略阈值，也不会下载任意域名资源。

## 已验证

- `https://voss.itougu.com` 的 HLS 播放列表可访问；
- FFmpeg 可从播放列表抽取音频；
- worker 只允许 HTTPS 的 `voss.itougu.com`，并限制时长与输出字节数；
- worker 默认只做探测；指定 `--audio-out` 才抽取音频。

## 运行边界

worker 应运行在 owner 的大容量节点，不能运行在 edge relay 小盘上。百度网盘归档
仍使用现有的可恢复分片上传器；上传成功后删除临时文件。Whisper 作为独立的可选
后处理依赖，缺少模型或依赖时任务应标记为可重试失败，不得影响飞书转发和行情采集。

示例（仅探测，不下载媒体）：

```bash
python3 scripts/itougu_video_worker.py \
  'https://voss.itougu.com/path/video.m3u8' --probe
```

完整视频归档和 Whisper 服务只有在 owner 的实际挂载点、FFmpeg、模型、百度
OAuth 及小文件上传验收通过后才启用；当前 40G/约 12G 可用的小盘节点不满足该
条件。
