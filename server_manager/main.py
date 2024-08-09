from __future__ import annotations
import hmac
import pandas
import streamlit
import streamlit.delta_generator
import altair

from server_manager.vbox import Metrics, VBoxManage, VMState, VirtualMachine


vbox = VBoxManage()


def main():
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
    vm_info = vm.info
    state = vm.state

    if state == VMState.Running:
        status = "🟢"
    elif state == VMState.PowerOff:
        status = "🔴"
    else:
        status = "⚠️"

    with tab.expander(
        f"🖥 {status} **{vm.name}**  ({vm_info['ostype']})  `{{{vm.id}}}`"
    ):
        vm_status_tab, vm_console_tab, vm_info_tab = streamlit.tabs(
            ["Status", "Console", "Info"]
        )
        virtualbox_manager_status_tab(vm_status_tab, vm)

        vm_info_tab.json(vm.info)


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
    status = vm.state

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


def server_console_tab(tab: streamlit.delta_generator.DeltaGenerator) -> None:
    tab.write("Server Console")


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
