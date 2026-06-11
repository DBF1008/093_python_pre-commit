from __future__ import annotations

import json
import os
from typing import Any

from pre_commit import output
from pre_commit.all_languages import languages
from pre_commit.clientlib import load_config
from pre_commit.hook import Hook
from pre_commit.lang_base import environment_dir
from pre_commit.repository import all_hooks
from pre_commit.store import Store


def _diagnose_hook(hook: Hook) -> dict[str, Any]:
    lang = languages[hook.language]

    info: dict[str, Any] = {
        'id': hook.id,
        'name': hook.name,
        'language': hook.language,
        'language_version': hook.language_version,
        'additional_dependencies': list(hook.additional_dependencies),
    }

    if lang.ENVIRONMENT_DIR is None:
        info['install_dir'] = None
        info['healthy'] = None
    else:
        env_dir = environment_dir(
            hook.prefix, lang.ENVIRONMENT_DIR, hook.language_version,
        )
        info['install_dir'] = env_dir

        if not os.path.exists(env_dir):
            info['healthy'] = 'not installed'
        else:
            health_error = lang.health_check(
                hook.prefix, hook.language_version,
            )
            if health_error:
                info['healthy'] = health_error
            else:
                info['healthy'] = True

    return info


def diagnose(
        config_file: str,
        store: Store,
        *,
        output_json: bool = False,
) -> int:
    config = load_config(config_file)
    hooks = all_hooks(config, store)

    # Group hooks by repo config order
    repo_groups: list[dict[str, Any]] = []
    hook_idx = 0
    for repo_config in config['repos']:
        repo = repo_config['repo']
        rev = repo_config.get('rev')
        num_hooks = len(repo_config['hooks'])
        group_hooks = hooks[hook_idx:hook_idx + num_hooks]
        hook_idx += num_hooks

        hook_infos = [_diagnose_hook(h) for h in group_hooks]
        repo_groups.append({
            'repo': repo,
            'rev': rev,
            'hooks': hook_infos,
        })

    if output_json:
        output.write_line(json.dumps(repo_groups, indent=2))
    else:
        _print_table(repo_groups)

    return 0


def _format_healthy(healthy: bool | str | None) -> str:
    if healthy is None:
        return 'n/a'
    elif healthy is True:
        return 'healthy'
    else:
        return f'UNHEALTHY - {healthy}'


def _print_table(repo_groups: list[dict[str, Any]]) -> None:
    for i, group in enumerate(repo_groups):
        if i > 0:
            output.write_line('')

        output.write_line(f'repo: {group["repo"]}')
        if group['rev'] is not None:
            output.write_line(f'rev:  {group["rev"]}')

        for hook_info in group['hooks']:
            output.write_line(f'  hook: {hook_info["id"]}')
            output.write_line(f'    name:            {hook_info["name"]}')
            output.write_line(f'    language:        {hook_info["language"]}')
            output.write_line(f'    version:         {hook_info["language_version"]}')

            deps = ', '.join(hook_info['additional_dependencies']) or '(none)'
            output.write_line(f'    additional_deps: {deps}')

            install_dir = hook_info['install_dir']
            output.write_line(
                f'    install_dir:     {install_dir or "n/a"}',
            )

            output.write_line(
                f'    healthy:         {_format_healthy(hook_info["healthy"])}',
            )
