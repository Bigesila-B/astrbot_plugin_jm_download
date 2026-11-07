import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.api import message_components as Comp


@register("astrbot_plugin_jm_download", "Bigesila-B", "JM漫画下载插件 - 支持下载禁漫天堂漫画为PDF并发送到QQ", "1.0.1")
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
            download_task = asyncio.create_task(
                asyncio.to_thread(self._download_comic, comic_id)
            )
            try:
                pdf_path, title, task_dir = await asyncio.wait_for(
                    asyncio.shield(download_task), timeout=timeout,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                # to_thread 超时后不会停止线程；完成后仅回收该任务的产物。
                download_task.add_done_callback(self._discard_download)
                raise

            try:
                file_name = os.path.basename(pdf_path)
                yield event.plain_result(f"下载完成: {title}\n正在发送PDF文件...")

                chain = [
                    Comp.Plain(f"{title}"),
                    Comp.File(file=pdf_path, name=file_name),
                ]
                yield event.chain_result(chain)
            finally:
                if not self.config.get("keep_files", False):
                    self._cleanup(task_dir)

        except asyncio.TimeoutError:
            yield event.plain_result(
                f"下载超时（>{timeout}秒），请检查网络或尝试其他漫画ID"
            )
        except Exception as e:
            logger.error(f"下载漫画失败: {e}", exc_info=True)
            yield event.plain_result(f"下载出错: {str(e)}")

    def _download_comic(self, comic_id: str):
        """在独立任务目录下载，返回 (pdf_path, title, task_dir)。"""
        import jmcomic

        download_root = Path(self.config.get("download_dir", "data/jm_downloads")).expanduser().resolve()
        download_root.mkdir(parents=True, exist_ok=True)
        task_dir = Path(tempfile.mkdtemp(prefix=f"jm_{comic_id}_", dir=download_root))
        option_path = None
        try:
            pdf_dir = task_dir / "pdf"
            pdf_dir.mkdir()
            domain = self.config.get("domain", "")
            domain_line = f"\n  domain: {json.dumps(domain, ensure_ascii=False)}" if domain else ""
            # JSON 字符串也是合法 YAML 字符串，避免 Windows 路径/特殊字符被误解析。
            option_yml = f"""dir_rule:
  rule: Bd_Aid
  base_dir: {json.dumps(str(task_dir), ensure_ascii=False)}
client:
  impl: api{domain_line}
download:
  image:
    suffix: .jpg
"""

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yml", delete=False, encoding="utf-8", dir=task_dir
            ) as f:
                f.write(option_yml)
                option_path = f.name

            option = jmcomic.create_option_by_file(option_path)
            jmcomic.download_album(
                comic_id,
                option,
                extra=jmcomic.Feature.export_pdf(
                    pdf_dir=str(pdf_dir), delete_original_file=True,
                ),
            )

            # 只接受本次导出目录中唯一的 PDF，绝不扫描共享目录或历史任务。
            pdf_files = [path for path in pdf_dir.rglob("*.pdf") if path.is_file()]
            if not pdf_files:
                raise FileNotFoundError(f"JM{comic_id} PDF 导出失败：本次任务未生成 PDF")
            if len(pdf_files) != 1:
                raise RuntimeError(f"JM{comic_id} 导出了多个 PDF，无法确定要发送的文件")
            pdf_path = pdf_files[0].resolve()
            if not pdf_path.is_relative_to(pdf_dir.resolve()):
                raise RuntimeError("PDF 路径不在本次任务的导出目录中")
            return str(pdf_path), pdf_path.stem, str(task_dir)
        except Exception:
            self._cleanup(task_dir)
            raise
        finally:
            if option_path and os.path.exists(option_path):
                os.unlink(option_path)

    def _discard_download(self, task):
        """回收已超时或已取消请求在后台完成的下载。"""
        if task.cancelled():
            return
        try:
            _, _, task_dir = task.result()
        except Exception:
            # 下载失败时 _download_comic 已回收目录，并在此取走任务异常。
            return
        self._cleanup(task_dir)

    def _cleanup(self, task_dir):
        """仅删除下载根目录内由本插件创建的单个任务目录。"""
        try:
            root = Path(self.config.get("download_dir", "data/jm_downloads")).expanduser().resolve()
            task_dir = Path(task_dir).resolve()
            if task_dir.parent != root or not task_dir.name.startswith("jm_"):
                raise ValueError("拒绝清理下载根目录或任务目录以外的路径")
            if task_dir.exists():
                shutil.rmtree(task_dir)
        except Exception as e:
            logger.warning(f"清理文件失败: {e}")

    async def terminate(self):
        """卸载时不删除配置目录，避免误删其他文件。"""
        pass
