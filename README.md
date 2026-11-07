# astrbot_plugin_jm_download

AstrBot 插件：通过 QQ 命令下载禁漫天堂(18comic)漫画为 PDF 并发送给用户。

## 功能

- 发送 `/jm <漫画ID>` 即可自动下载指定漫画、导出为 PDF 并发送
- 支持在 AstrBot WebUI 中可视化配置（下载目录、域名、保留文件等）
- 基于 [JMComic-Crawler-Python](https://github.com/hect0x7/JMComic-Crawler-Python) API

## 安装

### 方法一：WebUI 插件市场

在 AstrBot WebUI 插件市场搜索 `astrbot_plugin_jm_download` 安装。

### 方法二：手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/Bigesila-B/astrbot_plugin_jm_download
```

安装依赖：

```bash
pip install jmcomic
```

或重启 AstrBot，插件将自动安装 `requirements.txt` 中的依赖。

## 使用方法

发送以下命令：

```
/jm <漫画ID>
```

例如：

```
/jm 438696
```

Bot 将会：
1. 从禁漫天堂下载该漫画的所有章节图片
2. 自动合并为 PDF 文件
3. 将 PDF 发送到当前会话

## 配置项

在 AstrBot WebUI → 插件管理 → JM漫画下载 → 配置中可设置：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `download_dir` | string | `data/jm_downloads` | 漫画下载临时目录 |
| `keep_files` | bool | `false` | 是否保留下载的PDF文件 |
| `timeout` | int | `300` | 下载超时时间(秒) |
| `domain` | string | `""` | 自定义禁漫域名，留空自动选择 |

## v1.0.1 修复

- 每次请求在 `download_dir` 下创建独立任务目录，图片和 PDF 均写入该目录，支持同一本漫画的并发请求。
- 仅发送本次任务导出的唯一 PDF；缺少或出现多个 PDF 时明确报错，不再回退选择历史文件。
- `keep_files` 开启时，成功下载的 PDF 保留在各任务目录内；关闭时发送后清理该任务目录。
- 失败任务自动清理。下载超时只结束等待，不会强制停止下载线程；后台完成后回收该任务产物，不发送文件。

离线回归测试（无需 AstrBot、jmcomic 或外部网络）：

```bash
python -m unittest discover -s tests -v
```

## 注意事项

- 需要网络能访问禁漫天堂
- QQ 平台发送文件有大小限制（通常约 20MB），超大本子可能发送失败
- 请勿频繁大量下载，珍惜 JM 服务器资源
- 使用本插件时请尊重作品权利和相关平台的使用条款

## 依赖

- [jmcomic](https://github.com/hect0x7/JMComic-Crawler-Python) >= 2.6.19
- AstrBot >= v4.5.0

## 许可证

MIT License
