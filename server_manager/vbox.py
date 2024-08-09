from __future__ import annotations
from contextlib import contextmanager, suppress
from enum import Enum
from pathlib import Path
import re
from subprocess import CompletedProcess
import subprocess
import threading
import time
from typing import Any, Callable, Generator, TypeVar

from altair import Literal
import numpy


class Metrics(Enum):
    GUEST_CPU_LOAD_USER = "Guest/CPU/Load/User"
    GUEST_CPU_LOAD_KERNEL = "Guest/CPU/Load/Kernel"
    GUEST_RAM_USAGE_TOTAL = "Guest/RAM/Usage/Total"
    GUEST_RAM_USAGE_FREE = "Guest/RAM/Usage/Free"
    DISK_USAGE_USED = "Disk/Usage/Used"

    GUEST_RAM_USAGE_CACHE = "Guest/RAM/Usage/Cache"


T = TypeVar("T")


class VMState(Enum):

    Running = "running"
    PowerOff = "poweroff"
    Paused = "paused"
    Saving = "saving"
    Saved = "saved"
    Restoring = "restoring"
    Aborted = "aborted"
    Other = "other"

    @classmethod
    def _missing_(cls, _value: str) -> VMState:
        return VMState.Other


class VirtualMachine:

    def __init__(self, manage: VBoxManage, id: str, name: str) -> None:
        self.manage = manage
        self.id = id
        self.name = name

    @property
    def info(self) -> dict[str, Any]:
        result = self.manage.run("showvminfo", self.id, "--machinereadable")

        def _() -> Generator[tuple[str, str], None, None]:
            for line in result.stdout.decode().splitlines():
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip('"')
                value = value.strip('"')
                yield key, value

        return dict(_())

    @property
    def state(self) -> VMState:
        return VMState(self.info.get("VMState", "").casefold())

    def query_metric(self, name: str, converter: Callable[[str], T] = str) -> T:
        result = self.manage.run("metrics", "query", self.id, name)
        stdout = result.stdout.decode()
        metric_name_index = stdout.find(name)
        if metric_name_index == -1:
            return converter("nan")

        # Find end of line which contains the metric value.
        end_of_line_index = stdout.find("\n", metric_name_index)

        line_with_metric_name = stdout[metric_name_index:end_of_line_index]

        data = line_with_metric_name.replace(name, "").strip()
        if len(data) == 0:
            return converter("nan")

        return converter(data)

    @property
    def _metrics(self) -> dict[str, dict[str | Metrics, list[float]]]:
        return self.manage.metric_daemon.metrics

    def get_metric_history(
        self, metric: Metrics | Literal["time_stamp"]
    ) -> list[float]:
        return self._metrics[self.id][metric]


class VBoxManage:

    def __init__(self, executable: Path = Path("/usr/bin/vboxmanage")) -> None:
        self.executable = executable
        self.metric_daemon = VboxMetricDaemon(self)

    def run(
        self, *args: str, capture_output: bool = True, **kwargs: Any
    ) -> CompletedProcess:
        return subprocess.run(
            [self.executable.as_posix(), *args],
            executable=self.executable.as_posix(),
            capture_output=capture_output,
            **kwargs,
        )

    def list_vm(self) -> Generator[VirtualMachine, None, None]:
        result = self.run("list", "vms")
        assert result.returncode == 0, result.returncode

        for line in result.stdout.decode().splitlines():
            match = re.match(r'"(.+)" {(.+)}', line)
            if match:
                name, id = match.groups()
                yield VirtualMachine(self, id, name)

    def get_running_machines(self) -> list[VirtualMachine]:
        return [vm for vm in self.list_vm() if vm.state == VMState.Running]

    def metrics_enable(self) -> None:
        self.run("metrics", "enable")

    def metrics_setup(
        self, period: float = 0.5, samples: float = 1, *selectors: str
    ) -> None:
        self.run(
            "metrics",
            "setup",
            "--period",
            str(period),
            "--samples",
            str(samples),
            *selectors,
        )

    def metrics_collect(self) -> None:
        with suppress(subprocess.TimeoutExpired):
            self.run("metrics", "collect", capture_output=True, timeout=1)


T = TypeVar("T")


@contextmanager
def log_error() -> Generator[None, None, None]:
    try:
        yield
    except Exception as e:
        print(f"Error: {e!r}")
        raise


class VboxMetricDaemon:

    metrics: dict[str, dict[str | Metrics, list[float]]]

    def __init__(self, vbox: VBoxManage, interval_seconds: float = 0.2) -> None:
        self.vbox = vbox
        self.keep_alive = True
        self.interval_seconds = interval_seconds
        self.tick_number = 120

        self.metrics = {}
        self._refresh_metrics_storage()

        self.metric_query_thread = threading.Thread(target=self._query_metrics)
        self.metric_query_thread.start()

    def _refresh_metrics_storage(self) -> None:
        virtual_machines = self.vbox.list_vm()

        time_stamps = [
            float(value)
            for value in numpy.linspace(
                -(self.tick_number * self.interval_seconds),
                0,
                self.tick_number,
            )
        ]

        self.metrics = {
            vm.id: (
                {
                    Metrics.GUEST_CPU_LOAD_KERNEL: [float("nan")] * self.tick_number,
                    Metrics.GUEST_CPU_LOAD_USER: [float("nan")] * self.tick_number,
                    Metrics.GUEST_RAM_USAGE_TOTAL: [float("nan")] * self.tick_number,
                    Metrics.GUEST_RAM_USAGE_FREE: [float("nan")] * self.tick_number,
                    Metrics.DISK_USAGE_USED: [float("nan")] * self.tick_number,
                    Metrics.GUEST_RAM_USAGE_CACHE: [float("nan")] * self.tick_number,
                    "time_stamp": time_stamps,
                }
                if vm.id not in self.metrics
                else self.metrics[vm.id]
            )
            for vm in virtual_machines
        }

    def _query_metrics(self) -> None:
        while self.keep_alive:
            self.vbox.metrics_enable()
            self.vbox.metrics_collect()

            self._refresh_metrics_storage()

            for vm in self.vbox.list_vm():
                self.vbox.metrics_setup(self.interval_seconds, 1, vm.id)

                vm_metric_data = self.metrics.get(vm.id, {})

                with log_error():
                    vm_metric_data[Metrics.GUEST_CPU_LOAD_KERNEL].append(
                        vm.query_metric(
                            Metrics.GUEST_CPU_LOAD_KERNEL.value, parse_percent
                        ),
                    )
                    vm_metric_data[Metrics.GUEST_CPU_LOAD_KERNEL].pop(0)

                with log_error():
                    vm_metric_data[Metrics.GUEST_CPU_LOAD_USER].append(
                        vm.query_metric(
                            Metrics.GUEST_CPU_LOAD_USER.value, parse_percent
                        ),
                    )
                    vm_metric_data[Metrics.GUEST_CPU_LOAD_USER].pop(0)

                with log_error():
                    vm_metric_data[Metrics.GUEST_RAM_USAGE_TOTAL].append(
                        vm.query_metric(
                            Metrics.GUEST_RAM_USAGE_TOTAL.value, parse_bytes
                        ),
                    )
                    vm_metric_data[Metrics.GUEST_RAM_USAGE_TOTAL].pop(0)

                with log_error():
                    vm_metric_data[Metrics.GUEST_RAM_USAGE_FREE].append(
                        vm.query_metric(
                            Metrics.GUEST_RAM_USAGE_FREE.value, parse_bytes
                        ),
                    )
                    vm_metric_data[Metrics.GUEST_RAM_USAGE_FREE].pop(0)

                with log_error():
                    vm_metric_data[Metrics.DISK_USAGE_USED].append(
                        vm.query_metric(Metrics.DISK_USAGE_USED.value, parse_bytes),
                    )
                    vm_metric_data[Metrics.DISK_USAGE_USED].pop(0)

                with log_error():
                    vm_metric_data[Metrics.GUEST_RAM_USAGE_CACHE].append(
                        vm.query_metric(
                            Metrics.GUEST_RAM_USAGE_CACHE.value, parse_bytes
                        ),
                    )
                    vm_metric_data[Metrics.GUEST_RAM_USAGE_CACHE].pop(0)

                self.metrics[vm.id] = vm_metric_data

            time.sleep(self.interval_seconds)


def parse_percent(string) -> float:
    return float(string.strip("%"))


def parse_bytes(string: str) -> float:
    string = string.strip().casefold()
    if string.endswith(" b"):
        return float(string.rstrip("b"))
    if string.endswith(" kb"):
        return float(string.rstrip("kb")) * 1024
    if string.endswith(" mb"):
        return float(string.rstrip("mb")) * 1024 * 1024
    if string.endswith(" gb"):
        return float(string.rstrip("gb")) * 1024 * 1024 * 1024

    return float(string)
