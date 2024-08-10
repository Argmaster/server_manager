from __future__ import annotations
import getpass
import hmac
from pathlib import Path
import platform
from pprint import pformat
import subprocess
import sys
import time
from typing import Optional
from attr import dataclass
import pandas
import streamlit
import streamlit.delta_generator
import altair

import server_manager
from server_manager.logger import configure_host_logger, get_logger
from server_manager.vbox import Metrics, VBoxManage, VMState, VirtualMachine


configure_host_logger()
logger = get_logger()
logger.info(f"Server Manager ({server_manager.__version__}) started.")
logger.info(f"Streamlit version: {streamlit.__version__}")
logger.info(f"Python executable: {sys.executable}")
logger.info(platform.platform())
logger.info(pformat(sys.argv))

vbox = VBoxManage()


def main():
    logger.info("Executing Streamlit app.")

    streamlit.title("Server Manager")

    if not check_password():
        streamlit.stop()  # Do not continue if check_password is not True.

    virtualbox_tab, console_tab = streamlit.tabs(["VirtualBox", "Console"])

    virtualbox_manager_tab(virtualbox_tab)
    server_console_tab(console_tab)


def virtualbox_manager_tab(tab: streamlit.delta_generator.DeltaGenerator) -> None:
    tab.write("Virtual Machines List")

    for vm in vbox.list_vm():
        virtualbox_manager_tab_virtual_machine(tab.empty(), vm)


def virtualbox_manager_tab_virtual_machine(tab, vm: VirtualMachine):
    vm.info.reload()

    with tab.expander(f"🖥 **{vm.name}**  `{vm.info.system}`  `{vm.id}`"):
        vm_status_tab, vm_info_tab = streamlit.tabs(["Status", "Info"])
        virtualbox_manager_status_tab(vm_status_tab, vm)

        vm_info_tab.dataframe(
            vm.info.items(), use_container_width=True, hide_index=True
        )


def virtualbox_manager_status_tab(
    container: streamlit.delta_generator.DeltaGenerator, vm: VirtualMachine
) -> None:
    inner = container.container()

    _vm_status_message(inner.empty(), vm)

    _vm_control_buttons(inner.empty(), vm)

    virtualbox_manager_metric_plot(
        inner.empty(), vm, Metrics.GUEST_CPU_LOAD_USER, "CPU % (user)"
    )
    virtualbox_manager_metric_plot(
        inner.empty(), vm, Metrics.GUEST_CPU_LOAD_KERNEL, "CPU % (kernel)"
    )
    virtualbox_manager_metric_plot_ram(inner.empty(), vm)


@streamlit.fragment(run_every=5)
def _vm_status_message(
    container: streamlit.delta_generator.DeltaGenerator, vm: VirtualMachine
) -> None:
    logger.debug("Reloading '%s' status.", vm.name)

    vm.info.reload()
    status = vm.info.state

    {
        VMState.Running: lambda: container.success("🟢 Running"),
        VMState.PowerOff: lambda: container.error("🔴 Power Off"),
        VMState.Paused: lambda: container.info("🔵 Paused"),
        VMState.Saved: lambda: container.info("💾 Saved"),
    }.get(status, lambda: container.warning(f"⚠️ {status.name}"))()


def _vm_control_buttons(
    container: streamlit.delta_generator.DeltaGenerator,
    vm: VirtualMachine,
) -> None:
    container = container.container()

    start, shutdown, kill = container.columns(3)

    def _start():
        streamlit.toast(f"🔵 Starting `{vm.name}`...")
        vm.start()

        time.sleep(1)
        vm.info.reload()
        if vm.info.state == VMState.Running:
            streamlit.toast(f"🟢 Started `{vm.name}`...")
        else:
            streamlit.toast(f"⛔ Failed to start `{vm.name}`...")

    start.button(
        "🟢 Start", on_click=_start, key=f"start_{vm.id}", use_container_width=True
    )

    def _shutdown():
        streamlit.toast(f"🔵 Shutting down `{vm.name}`...")
        vm.shutdown()

        time.sleep(1)
        vm.info.reload()
        if vm.info.state == VMState.PowerOff:
            streamlit.toast(f"🔴 Shut down `{vm.name}`...")
        else:
            streamlit.toast(f"⛔ Failed to shut down `{vm.name}`...")

    shutdown.button(
        "🔴 Shutdown",
        on_click=_shutdown,
        key=f"shutdown_{vm.id}",
        use_container_width=True,
    )

    def _kill():
        streamlit.toast(f"💀 Killing `{vm.name}`...")
        vm.kill()

        time.sleep(1)
        vm.info.reload()
        if vm.info.state == VMState.PowerOff:
            streamlit.toast(f"💀 Killed `{vm.name}`...")
        else:
            streamlit.toast(f"⛔ Failed to kill `{vm.name}`...")

    kill.button(
        "💀 Kill", on_click=_kill, key=f"kill_{vm.id}", use_container_width=True
    )

    pause, save, resume = container.columns(3)

    def _pause():
        streamlit.toast(f"🔵 Pausing `{vm.name}`...")
        vm.pause()

        time.sleep(1)
        vm.info.reload()
        if vm.info.state == VMState.Paused:
            streamlit.toast(f"🔵 Paused `{vm.name}`...")
        else:
            streamlit.toast(f"⛔ Failed to pause `{vm.name}`...")

    pause.button(
        "🔵 Pause", on_click=_pause, key=f"pause_{vm.id}", use_container_width=True
    )

    def _save():
        streamlit.toast(f"🔵 Saving `{vm.name}`...")
        vm.save()
        time.sleep(1)

        vm.info.reload()
        if vm.info.state == VMState.Saved:
            streamlit.toast(f"🔵 Saved `{vm.name}`...")
        else:
            streamlit.toast(f"⛔ Failed to save `{vm.name}`...")

    save.button(
        "💾 Save", on_click=_save, key=f"save_{vm.id}", use_container_width=True
    )

    def _resume():
        streamlit.toast(f"🟢 Resuming `{vm.name}`...")
        vm.resume()
        time.sleep(1)

        vm.info.reload()
        if vm.info.state == VMState.Running:
            streamlit.toast(f"🟢 Resumed `{vm.name}`...")
        else:
            streamlit.toast(f"⛔ Failed to resume `{vm.name}`...")

    resume.button(
        "🟢 Resume", on_click=_resume, key=f"resume_{vm.id}", use_container_width=True
    )


@streamlit.fragment(run_every=5)
def virtualbox_manager_metric_plot(
    container: streamlit.delta_generator.DeltaGenerator,
    vm: VirtualMachine,
    metric: Metrics,
    y_name: str,
):
    logger.debug("Reloading '%s' metric '%s' plot.", vm.name, metric.value)

    x_values = vm.get_metric_history("time_stamp")
    y_values = vm.get_metric_history(metric)

    x_name = "Time"
    df = pandas.DataFrame({x_name: x_values, y_name: y_values})

    chart = (
        altair.Chart(df, title=y_name, height=300)
        .mark_line()
        .encode(
            x=altair.X(x_name),
            y=altair.Y(y_name).scale(domain=(0, 100)),
        )
    )
    container.altair_chart(chart, use_container_width=True)


@streamlit.fragment(run_every=5)
def virtualbox_manager_metric_plot_ram(
    container: streamlit.delta_generator.DeltaGenerator,
    vm: VirtualMachine,
):
    logger.debug("Reloading '%s' RAM usage plot.")

    x_values = vm.get_metric_history("time_stamp")
    y_values = [
        (total - free) / (1024 * 1024)
        for (total, free) in zip(
            vm.get_metric_history(Metrics.GUEST_RAM_USAGE_TOTAL),
            vm.get_metric_history(Metrics.GUEST_RAM_USAGE_FREE),
        )
    ]

    x_name = "Time"
    y_name = "RAM (MB)"
    df = pandas.DataFrame({x_name: x_values, y_name: y_values})

    chart = (
        altair.Chart(df, title=y_name, height=300)
        .mark_line()
        .encode(
            x=altair.X(x_name),
            y=altair.Y(y_name),
        )
    )
    container.altair_chart(chart, use_container_width=True)


@dataclass
class CommandResult:

    command: str
    return_code: Optional[int]
    stdout: str
    stderr: str
    is_timeout: bool


def server_console_tab(tab: streamlit.delta_generator.DeltaGenerator) -> None:
    tab.write("Server Console")
    tab.container()

    tab.button(
        "Clear command history",
        on_click=lambda: streamlit.session_state.pop("host_command_results", None),
        use_container_width=True,
    )
    command_string = tab.text_input(
        "Command",
        placeholder=f"$ ({getpass.getuser()}) {Path.cwd().as_posix()}",
        on_change=print(),
        key="command",
    )
    if command_string:
        try:
            result = subprocess.run(
                command_string,
                shell=True,
                capture_output=True,
                timeout=streamlit.session_state.get("command_timeout_seconds", 3600),
            )
        except subprocess.TimeoutExpired as e:
            host_command_results = streamlit.session_state.get(
                "host_command_results", []
            )
            host_command_results.insert(
                0,
                CommandResult(
                    command=command_string,
                    return_code=None,
                    stdout=e.stdout.decode("utf-8") if e.stdout else "",
                    stderr=e.stderr.decode("utf-8") if e.stderr else "",
                    is_timeout=True,
                ),
            )
            streamlit.session_state["host_command_results"] = host_command_results

        host_command_results = streamlit.session_state.get("host_command_results", [])
        host_command_results.insert(
            0,
            CommandResult(
                command=result.args,
                return_code=result.returncode,
                stdout=result.stdout.decode("utf-8"),
                stderr=result.stderr.decode("utf-8"),
                is_timeout=False,
            ),
        )
        streamlit.session_state["host_command_results"] = host_command_results

    command_timeout_seconds = tab.number_input(
        "Timeout (seconds)", value=3600, min_value=0
    )
    if command_timeout_seconds:
        streamlit.session_state["command_timeout_seconds"] = command_timeout_seconds

    command_log_container = tab.container()

    for result in streamlit.session_state.get("host_command_results", []):
        assert isinstance(result, CommandResult)
        with command_log_container.expander(
            f"[**`{result.return_code}`**] {'[timeout]' if result.is_timeout else ''} `{result.command}`"
        ):
            streamlit.subheader("Command")
            streamlit.code(f"{result.command}")
            streamlit.subheader("Return Code")
            streamlit.code(f"{result.return_code}")
            streamlit.subheader("stdout")
            streamlit.code(result.stdout)
            streamlit.subheader("stderr")
            streamlit.code(result.stderr)


def check_password():
    """Returns `True` if the user had the correct password."""
    if not streamlit.secrets["password"]:
        return True

    def password_entered():
        """Checks whether a password entered by the user is correct."""
        if hmac.compare_digest(
            streamlit.session_state["password"], streamlit.secrets["password"]
        ):
            streamlit.session_state["password_correct"] = True
            del streamlit.session_state["password"]  # Don't store the password.
        else:
            streamlit.session_state["password_correct"] = False

    # Return True if the password is validated.
    if streamlit.session_state.get("password_correct", False):
        return True

    # Show input for password.
    streamlit.text_input(
        "Password", type="password", on_change=password_entered, key="password"
    )
    if "password_correct" in streamlit.session_state:
        streamlit.error("😕 Password incorrect")
    return False
