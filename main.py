import asyncio
import os
import tempfile
from pathlib import Path

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.api import message_components as Comp


@register("astrbot_plugin_jm_download", "Bigesila-B", "JM漫画下载插件 - 支持下载禁漫天堂漫画为PDF并发送到QQ", "1.0.0")
class JMDownloadPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    @filter.command("jm")
    async def jm_download(self, event: AstrMessageEvent):
        """下载禁漫天堂漫画: /jm <漫画ID>"""
        msg = event.message_str.strip()
        parts = msg.split()
        if len(parts) < 2:
            yield event.plain_result("请提供漫画ID，用法: /jm <漫画ID>\n例如: /jm 438696")
            return

        comic_id = parts[1].strip()
        if not comic_id.isdigit():
            yield event.plain_result("漫画ID必须是数字，例如: /jm 438696")
            return

        yield event.plain_result(f"正在下载漫画 JM{comic_id}，请稍候...")

        try:
            timeout = self.config.get("timeout", 300)
            pdf_path, title = await asyncio.wait_for(
                asyncio.to_thread(self._download_comic, comic_id),
                timeout=timeout,
            )

            file_name = os.path.basename(pdf_path)

            yield event.plain_result(f"下载完成: {title}\n正在发送PDF文件...")

            chain = [
                Comp.Plain(f"{title}"),
                Comp.File(file=pdf_path, name=file_name),
            ]
            yield event.chain_result(chain)

            if not self.config.get("keep_files", False):
                self._cleanup(pdf_path)

        except asyncio.TimeoutError:
            yield event.plain_result(
                f"下载超时（>{timeout}秒），请检查网络或尝试其他漫画ID"
            )
        except Exception as e:
            logger.error(f"下载漫画失败: {e}", exc_info=True)
            yield event.plain_result(f"下载出错: {str(e)}")

    def _download_comic(self, comic_id: str):
        """在同步线程中下载漫画并导出为 PDF，返回 (pdf_path, title) 或 raises Exception"""
        import jmcomic

        download_dir = self.config.get("download_dir", "data/jm_downloads")
        os.makedirs(download_dir, exist_ok=True)

        domain = self.config.get("domain", "")
        domain_line = f"\n  domain: {domain}" if domain else ""

        option_yml = f"""dir_rule:
  rule: Bd_Aid
  base_dir: {download_dir}
client:
  impl: api{domain_line}
download:
  image:
    suffix: .jpg
"""

        option_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yml", delete=False, encoding="utf-8"
            ) as f:
                f.write(option_yml)
                option_path = f.name

            option = jmcomic.create_option_by_file(option_path)
            jmcomic.download_album(
                comic_id,
                option,
                extra=jmcomic.Feature.export_pdf(delete_original_file=True),
            )

            # 查找生成的 PDF 文件（递归搜索子目录）
            pdf_files = sorted(
                Path(download_dir).rglob(f"*{comic_id}*.pdf"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not pdf_files:
                pdf_files = sorted(
                    Path(download_dir).rglob("*.pdf"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )

            if pdf_files:
                pdf_path = str(pdf_files[0])
                title = pdf_files[0].stem
                return (pdf_path, title)

            # 下载完成但没找到 PDF，列出目录内容帮助排查
            all_files = list(Path(download_dir).rglob("*"))
            file_list = "\n".join(f"  {f}" for f in all_files[:20])
            logger.error(
                f"JM{comic_id} 下载后未找到 PDF 文件。"
                f"目录 {download_dir} 内容:\n{file_list or '  (空)'}"
            )
            raise FileNotFoundError(f"JM{comic_id} PDF 导出失败")
        except Exception:
            raise
        finally:
            if option_path and os.path.exists(option_path):
                os.unlink(option_path)

    def _cleanup(self, pdf_path: str):
        """清理下载的 PDF 文件及其空目录"""
        try:
            if os.path.isfile(pdf_path):
                os.remove(pdf_path)
            parent = os.path.dirname(pdf_path)
            if os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)
        except Exception as e:
            logger.warning(f"清理文件失败: {e}")

    async def terminate(self):
        """卸载时不删除配置目录，避免误删其他文件。"""
        pass
