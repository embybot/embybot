# -*- coding: utf-8 -*-

import os
import shutil
import glob
import re

from .. import i18n
from ..core.config import get_setting

def delete_media_files(item_path: str, delete_local: bool = False, delete_cloud: bool = False) -> str:
    print(i18n._("🗑️ Requesting file deletion, Emby path: {path}, ℹ️ Local: {local}, ℹ️ Cloud: {cloud}").format(
        path=item_path, local=delete_local, cloud=delete_cloud
    ))
    media_base_path = get_setting('settings.media_base_path')
    media_cloud_path = get_setting('settings.media_cloud_path')
    
    if item_path and os.path.splitext(item_path)[1]:
        item_path = os.path.dirname(item_path)

    if not media_base_path or not item_path or not item_path.startswith(media_base_path):
        error_msg = i18n._("❌ Error: Item path '{item_path}' does not match or is invalid with base path '{base_path}'.").format(
            item_path=item_path, base_path=media_base_path
        )
        print(f"❌ {error_msg}")
        return error_msg

    relative_path = os.path.relpath(item_path, media_base_path)
    log = []

    if delete_local:
        base_target_dir = os.path.join(media_base_path, relative_path)
        if os.path.isdir(base_target_dir):
            try:
                shutil.rmtree(base_target_dir)
                msg = i18n._("✅ Successfully deleted local directory: {path}").format(path=base_target_dir)
                log.append(msg)
                print(msg)
            except Exception as e:
                msg = i18n._("❌ Failed to delete local directory: {error}").format(error=e)
                log.append(msg)
                print(i18n._("❌ Error deleting local directory '{path}': {error}").format(path=base_target_dir, error=e))
        else:
            log.append(i18n._("🟡 Local directory not found: {path}").format(path=base_target_dir))
    
    if delete_cloud:
        if not media_cloud_path:
            return i18n._("❌ Operation failed: Cloud drive path (media_cloud_path) is not set in the configuration.")
            
        cloud_target_dir = os.path.join(media_cloud_path, relative_path)
        if os.path.isdir(cloud_target_dir):
            try:
                shutil.rmtree(cloud_target_dir)
                msg = i18n._("✅ Successfully deleted cloud directory: {path}").format(path=cloud_target_dir)
                log.append(msg)
                print(msg)
            except Exception as e:
                msg = i18n._("❌ Failed to delete cloud directory: {error}").format(error=e)
                log.append(msg)
                print(i18n._("⚠️ Warning: Failed to delete cloud path '{path}': {error}").format(path=cloud_target_dir, error=e))
        else:
            log.append(i18n._("🟡 Cloud directory not found: {path}").format(path=cloud_target_dir))

    if not log:
        return i18n._("🤷 No deletion operations were performed.")

    return i18n._("✅ Deletion operation complete:") + "\n" + "\n".join(log)


def update_media_files(item_path: str) -> str:
    print(i18n._("🔄 Requesting media update, Emby path: {path}").format(path=item_path))
    media_base_path = get_setting('settings.media_base_path')
    media_cloud_path = get_setting('settings.media_cloud_path')

    if not media_base_path or not media_cloud_path:
        error_msg = i18n._("❌ Error: `media_base_path` or `media_cloud_path` is not set in the configuration.")
        print(f"❌ {error_msg}")
        return error_msg

    if not item_path.startswith(media_base_path):
        error_msg = i18n._("❌ Error: Item path '{item_path}' does not match base path '{base_path}'.").format(
            item_path=item_path, base_path=media_base_path
        )
        print(f"❌ {error_msg}")
        return error_msg

    relative_path = item_path.replace(media_base_path, "").lstrip('/')
    source_dir = os.path.join(media_cloud_path, relative_path)
    target_dir = os.path.join(media_base_path, relative_path)

    if not os.path.isdir(source_dir):
        error_msg = i18n._("❌ Error: Source directory '{path}' not found in the cloud drive.").format(path=source_dir)
        print(f"❌ {error_msg}")
        return error_msg

    os.makedirs(target_dir, exist_ok=True)
    
    metadata_extensions = {".nfo", ".jpg", ".jpeg", ".png", ".svg", ".ass", ".srt", ".sup", ".mp3", ".flac", ".aac", ".ssa", ".lrc"}
    update_log = []

    for root, _, files in os.walk(source_dir):
        for filename in files:
            source_file_path = os.path.join(root, filename)
            relative_subdir = os.path.relpath(root, source_dir)
            target_subdir = os.path.join(target_dir, relative_subdir) if relative_subdir != '.' else target_dir
            os.makedirs(target_subdir, exist_ok=True)

            file_ext = os.path.splitext(filename)[1].lower()

            if file_ext in metadata_extensions:
                target_file_path = os.path.join(target_subdir, filename)
                if not os.path.exists(target_file_path) or os.path.getmtime(source_file_path) > os.path.getmtime(target_file_path):
                    shutil.copy2(source_file_path, target_file_path)
                    update_log.append(i18n._("• Copied metadata: {filename}").format(filename=filename))
            else:
                strm_filename = os.path.splitext(filename)[0] + ".strm"
                strm_file_path = os.path.join(target_subdir, strm_filename)
                
                source_mtime = os.path.getmtime(source_file_path)

                if not os.path.exists(strm_file_path) or source_mtime > os.path.getmtime(strm_file_path):
                    action = i18n._("Created") if not os.path.exists(strm_file_path) else i18n._("Updated")
                    
                    with open(strm_file_path, 'w', encoding='utf-8') as f:
                        f.write(source_file_path)
                    
                    try:
                        os.utime(strm_file_path, (os.path.getatime(strm_file_path), source_mtime))
                    except Exception as e:
                        print(i18n._("⚠️ Warning: Failed to set file timestamp: {error}").format(error=e))

                    update_log.append(i18n._("• {action} link: {filename}").format(action=action, filename=strm_filename))

    if not update_log:
        return i18n._("✅ `/{path}` requires no update, files are already up to date.").format(path=relative_path)
        
    print(i18n._("✅ `/{path}` update complete.").format(path=relative_path))
    
    details = "\n".join(update_log)
    return i18n._("✅ `/{path}` has been updated successfully!\n\nChange details:\n{details}").format(path=relative_path, details=details)

def _series_base_dirs(series_path: str) -> tuple[str | None, str | None]:
    if not series_path:
        return None, None
    base = series_path
    if os.path.splitext(base)[1]:
        base = os.path.dirname(base)
    
    media_base_path = get_setting('settings.media_base_path')
    media_cloud_path = get_setting('settings.media_cloud_path')

    local_root = base if media_base_path and base.startswith(media_base_path) else None
    cloud_root = None
    if media_base_path and media_cloud_path and local_root:
        rel = os.path.relpath(local_root, media_base_path)
        cloud_root = os.path.join(media_cloud_path, rel)
        
    return local_root, cloud_root


def delete_local_cloud_seasons(series_path: str, seasons: list[int], *, delete_local=False, delete_cloud=False) -> str:
    if not delete_local and not delete_cloud:
        return i18n._("⚠️ No deletion target specified.")
        
    local_root, cloud_root = _series_base_dirs(series_path)
    logs = []

    def _do_dir(root: str, label: str):
        if not root:
            logs.append(i18n._("🟡 {label} root directory is unknown or not configured.").format(label=label))
            return
            
        for sn in seasons:
            candidates = [os.path.join(root, f"Season {sn}"), os.path.join(root, f"Season {sn:02d}")]
            deleted_any = False
            for d in candidates:
                if os.path.isdir(d):
                    try:
                        shutil.rmtree(d)
                        logs.append(i18n._("✅ Deleted {label} directory: {path}").format(label=label, path=d))
                        deleted_any = True
                    except Exception as e:
                        logs.append(i18n._("❌ Failed to delete {label} directory: {path} | {error}").format(label=label, path=d, error=e))
            if not deleted_any:
                logs.append(i18n._("🟡 {label} season directory not found: S{num:02d}").format(label=label, num=sn))

    if delete_local:
        _do_dir(local_root, i18n._("Local"))
    if delete_cloud:
        _do_dir(cloud_root, i18n._("Cloud"))
        
    return "\n".join(logs) if logs else i18n._("🤷 No deletion operations were performed.")


def delete_local_cloud_episodes(series_path: str, season_to_eps: dict[int, list[int]], *, delete_local=False, delete_cloud=False) -> str:
    if not delete_local and not delete_cloud:
        return i18n._("⚠️ No deletion target specified.")
        
    local_root, cloud_root = _series_base_dirs(series_path)
    logs = []

    def _do_files(root: str, label: str):
        if not root:
            logs.append(i18n._("🟡 {label} root directory is unknown or not configured.").format(label=label))
            return
            
        for sn, eps in sorted(season_to_eps.items()):
            season_dirs = [os.path.join(root, f"Season {sn}"), os.path.join(root, f"Season {sn:02d}")]
            found_dir = None
            for sd in season_dirs:
                if os.path.isdir(sd):
                    found_dir = sd
                    break
            
            if not found_dir:
                logs.append(i18n._("🟡 {label} season directory not found: S{num:02d}").format(label=label, num=sn))
                continue

            for e in eps:
                patterns = [
                    f"*S{sn:02d}E{e:02d}*", f"*S{sn:02d}E{e:03d}*",
                    f"*s{sn:02d}e{e:02d}*", f"*s{sn:02d}e{e:03d}*",
                ]
                matched_files = set()
                for p in patterns:
                    for path in glob.glob(os.path.join(found_dir, p)):
                        matched_files.add(path)

                if not matched_files:
                    logs.append(i18n._("🟡 {label} files not found: S{s:02d}E{e:02d}").format(label=label, s=sn, e=e))
                    continue

                for fp in sorted(list(matched_files)):
                    try:
                        if os.path.isdir(fp):
                            shutil.rmtree(fp)
                        else:
                            os.remove(fp)
                        logs.append(i18n._("✅ Deleted {label} file: {path}").format(label=label, path=fp))
                    except Exception as ex:
                        logs.append(i18n._("❌ Failed to delete {label} file: {path} | {error}").format(label=label, path=fp, error=ex))

    if delete_local:
        _do_files(local_root, i18n._("Local"))
    if delete_cloud:
        _do_files(cloud_root, i18n._("Cloud"))

    return "\n".join(logs) if logs else i18n._("🤷 No deletion operations were performed.")