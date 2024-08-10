from __future__ import annotations
import getpass
import hmac
from pathlib import Path
import platform
from pprint import pformat
import subprocess
import sys
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

    status = {
        VMState.Running: "🟢",
        VMState.PowerOff: "🔴",
    }.get(vm.info.state, "⚠️")

    with tab.expander(f"🖥 {status} **{vm.name}**  ({vm.info.system})  `{vm.id}`"):
        vm_status_tab, vm_info_tab = streamlit.tabs(["Status", "Info"])
        virtualbox_manager_status_tab(vm_status_tab, vm)

        vm_info_tab.dataframe(
            vm.info.items(), use_container_width=True, hide_index=True
        )


def virtualbox_manager_status_tab(
    vm_status_tab: streamlit.delta_generator.DeltaGenerator, vm: VirtualMachine
) -> None:
    _vm_status_message(vm_status_tab.empty(), vm)

    container = vm_status_tab.container()

    virtualbox_manager_metric_plot(
        container.empty(), vm, Metrics.GUEST_CPU_LOAD_USER, "CPU % (user)"
    )
    virtualbox_manager_metric_plot(
        container.empty(), vm, Metrics.GUEST_CPU_LOAD_KERNEL, "CPU % (kernel)"
    )
    virtualbox_manager_metric_plot_ram(container.empty(), vm)


@streamlit.fragment(run_every=5)
def _vm_status_message(
    container: streamlit.delta_generator.DeltaGenerator, vm: VirtualMachine
) -> None:
    logger.debug("Reloading '%s' status.", vm.name)

    vm.info.reload()
    status = vm.info.state

    if status == VMState.Running:
        container.success("🟢 Running")
    elif status == VMState.PowerOff:
        container.error("🔴 Power Off")
    else:
        container.warning(f"⚠️ {status.name}")


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
