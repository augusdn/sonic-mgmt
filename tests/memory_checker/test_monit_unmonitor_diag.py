"""Diagnostic aid for ADO 39182708 / 39220068 - not a regression test.

test_memory_checker fails because every container_memory_* monit program is left
in "Not monitored" state, which satisfies neither is_monit_mem_ok nor
is_monit_mem_failed, so wait_monit_mem_ok burns its full 700s timeout.

The only code in SONiC that unmonitors those programs is _stop_services() in
sonic-utilities config/main.py (PR 4710). This module repeats `config reload`
and records, after each one, the monit state of every container_memory_* program
alongside what monit itself wrote to syslog, so the unmonitor/monitor actions can
be read directly rather than inferred.
"""
import logging
import pytest

from tests.common.utilities import wait_until

pytestmark = [
    pytest.mark.topology('any'),
    pytest.mark.disable_loganalyzer,
]

logger = logging.getLogger(__name__)

MEM_PREFIX = "container_memory_"
RELOAD_ITERATIONS = 8
SYSLOG_FILTER = "sudo grep -aiE 'monit|memory_checker' /var/log/syslog | tail -n {}"


def _sh(duthost, cmd):
    return duthost.shell(cmd, module_ignore_errors=True, verbose=False).get("stdout", "")


def _monit_summary(duthost):
    return _sh(duthost, "sudo monit summary")


def _mem_states(duthost):
    """Map container_memory_* program name -> status column from `monit summary`."""
    states = {}
    for line in _monit_summary(duthost).splitlines():
        stripped = line.strip()
        if stripped.startswith(MEM_PREFIX):
            fields = stripped.split()
            states[fields[0]] = " ".join(fields[1:-1]) if len(fields) > 2 else ""
    return states


def _block(title, body):
    logger.warning("===== %s =====\n%s", title, body)


def test_monit_unmonitor_diag(duthosts, rand_one_dut_hostname):
    """Record monit state and monit's own syslog around repeated config reloads."""
    duthost = duthosts[rand_one_dut_hostname]

    _block("initial monit summary", _monit_summary(duthost))
    _block("initial syslog monit lines (covers testbed deploy-mg)",
           _sh(duthost, SYSLOG_FILTER.format(300)))

    for iteration in range(1, RELOAD_ITERATIONS + 1):
        before = _mem_states(duthost)
        logger.warning("iteration %d BEFORE reload: %s", iteration, before)

        duthost.shell("sudo truncate -s 0 /var/log/syslog", module_ignore_errors=True)

        result = duthost.shell("sudo config reload -y", module_ignore_errors=True)
        _block("iteration {} config reload rc={}".format(iteration, result.get("rc")),
               "--- stdout ---\n{}\n--- stderr ---\n{}".format(result.get("stdout"),
                                                               result.get("stderr")))

        wait_until(300, 20, 30, duthost.critical_services_fully_started)

        after = _mem_states(duthost)
        logger.warning("iteration %d AFTER reload: %s", iteration, after)

        _block("iteration {} syslog monit lines".format(iteration),
               _sh(duthost, SYSLOG_FILTER.format(400)))

        unmonitored = {name: st for name, st in after.items() if "Not monitored" in st}
        if unmonitored:
            logger.warning("REPRODUCED on iteration %d: %s", iteration, unmonitored)
            _block("monit summary at reproduction", _monit_summary(duthost))
            for name in unmonitored:
                _block("monit status {}".format(name),
                       _sh(duthost, "sudo monit status {}".format(name)))
            break
    else:
        logger.warning("NOT REPRODUCED across %d config reloads", RELOAD_ITERATIONS)
