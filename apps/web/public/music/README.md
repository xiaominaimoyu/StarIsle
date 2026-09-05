# 音乐资源目录

播放器仅播放**自有、授权或公版**音频样例。

- 将音频文件放入本目录(如 `sample.mp3`)
- 在 `apps/web/src/site-data/playlist.json` 的 `tracks` 中登记曲目(标题 / 艺人 / 文件路径 / 可选封面)
- 使用前确认授权来源并在总体开发计划的来源登记中记录

当前播放列表为空(`src/site-data/playlist.json`),播放器显示空态提示,不会伪造曲目。
