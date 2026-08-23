#!/usr/bin/env bash

# Shared device-log oracles for the physical XCUITest scenarios.  This file is
# intentionally source-only: callers own shell options, device leases and paths.

verify_lifecycle_oracle()
{
    local log_path="$1"
    local expected_pid="$2"
    local initial_sequence="${3:-0}"
    awk -v expected_pid="$expected_pid" -v initial_sequence="$initial_sequence" '
        function fail(message) {
            print "FAIL: lifecycle log oracle: " message > "/dev/stderr"
            failed = 1
            exit 1
        }
        function sequence_after(left, right) {
            if (length(left) != length(right))
                return length(left) > length(right)
            return ("x" left) > ("x" right)
        }
        function lifecycle_event(line, fields, pid, sequence, event) {
            if (line !~ /^\* iOS lifecycle v1 /)
                return ""
            if (line !~ /^\* iOS lifecycle v1 pid=[1-9][0-9]* seq=[1-9][0-9]* event=(deactivate|activate)$/)
                fail("invalid versioned lifecycle marker")
            split(line, fields, " ")
            pid = substr(fields[5], 5)
            sequence = substr(fields[6], 5)
            event = substr(fields[7], 7)
            if (pid != expected_pid)
                fail("lifecycle marker PID " pid " does not match expected PID " expected_pid)
            if (last_sequence != "" && !sequence_after(sequence, last_sequence))
                fail("lifecycle marker sequence " sequence " is not strictly increasing after " last_sequence)
            last_sequence = sequence
            return event
        }
        BEGIN {
            last_sequence = initial_sequence
        }
        function relevant_app_or_drawable(line) {
            return line ~ /iOS: app (deactivate|activate)/ || \
                line ~ /iOS: foreground drawable/
        }
        {
            event = lifecycle_event($0)
            if (event != "") {
                if (event == "deactivate") {
                    if (state != 1)
                        fail("deactivate before the previous group completed")
                    state = 2
                    next
                }
                if (state != 4)
                    fail("activate before foreground drawable")
                ++groups
                if (groups > 5)
                    fail("sixth complete lifecycle group")
                state = 0
                next
            }

            if ($0 == "* iOS: app deactivate") {
                if (state != 0)
                    fail("deactivate before the previous group completed")
                state = 1
                next
            }
            if ($0 == "* iOS: app activate") {
                if (state != 2)
                    fail("app activate before versioned deactivate")
                state = 3
                next
            }
            if ($0 == "* iOS: foreground drawable 1864x860 (engine 1864x860)") {
                if (state != 3)
                    fail("foreground drawable before app activate")
                state = 4
                next
            }
            if (relevant_app_or_drawable($0))
                fail("unexpected relevant lifecycle/app/drawable event")
        }
        END {
            if (failed)
                exit 1
            if (state != 0) {
                print "FAIL: lifecycle log oracle: truncated lifecycle group" > "/dev/stderr"
                exit 1
            }
            if (groups != 5) {
                print "FAIL: lifecycle log oracle: expected exactly 5 complete lifecycle groups, observed " groups > "/dev/stderr"
                exit 1
            }
        }
    ' "$log_path"
}

verify_audio_oracle()
{
    local log_path="$1"
    local expected_pid="$2"
    local initial_sequence="${3:-0}"
    local expected_lifecycle_groups="${4:-0}"
    awk -v expected_pid="$expected_pid" -v initial_sequence="$initial_sequence" -v expected_lifecycle_groups="$expected_lifecycle_groups" '
        function fail(message) {
            print "FAIL: audio log oracle: " message > "/dev/stderr"
            failed = 1
            exit 1
        }
        function sequence_after(left, right) {
            if (length(left) != length(right))
                return length(left) > length(right)
            return ("x" left) > ("x" right)
        }
        function lifecycle_event(line, fields, pid, sequence, event) {
            if (line !~ /^\* iOS lifecycle v1 /)
                return ""
            if (line !~ /^\* iOS lifecycle v1 pid=[1-9][0-9]* seq=[1-9][0-9]* event=(deactivate|activate)$/)
                fail("invalid versioned lifecycle marker")
            split(line, fields, " ")
            pid = substr(fields[5], 5)
            sequence = substr(fields[6], 5)
            event = substr(fields[7], 7)
            if (pid != expected_pid)
                fail("lifecycle marker PID " pid " does not match expected PID " expected_pid)
            if (last_sequence != "" && !sequence_after(sequence, last_sequence))
                fail("lifecycle marker sequence " sequence " is not strictly increasing after " last_sequence)
            last_sequence = sequence
            return event
        }
        BEGIN {
            last_sequence = initial_sequence
        }
        function relevant_app_or_drawable(line) {
            return line ~ /iOS: app (deactivate|activate)/ || \
                line ~ /iOS: foreground drawable/
        }
        function lifecycle_after_target() {
            if (target_complete)
                fail("lifecycle event after target audio interruption")
        }
        {
            event = lifecycle_event($0)
            if (event != "") {
                lifecycle_after_target()
                if (pair == "target")
                    fail("OpenXRay deactivated between audio interruption begin and end")
                if (event == "deactivate") {
                    if (lifecycle_state != 1)
                        fail("deactivate before the previous lifecycle group completed")
                    lifecycle_state = 2
                    next
                }
                if (lifecycle_state != 4)
                    fail("activate before foreground drawable")
                ++lifecycle_groups
                if (lifecycle_groups > expected_lifecycle_groups)
                    fail("more than expected complete lifecycle groups=" expected_lifecycle_groups)
                lifecycle_state = 0
                next
            }

            if ($0 == "* iOS: app deactivate") {
                lifecycle_after_target()
                if (pair == "target")
                    fail("OpenXRay deactivated between audio interruption begin and end")
                if (lifecycle_state != 0)
                    fail("deactivate before the previous lifecycle group completed")
                lifecycle_state = 1
                next
            }
            if ($0 == "* iOS: app activate") {
                lifecycle_after_target()
                if (pair == "target")
                    fail("OpenXRay activated between audio interruption begin and end")
                if (lifecycle_state != 2)
                    fail("app activate before versioned deactivate")
                lifecycle_state = 3
                next
            }
            if ($0 == "* iOS: foreground drawable 1864x860 (engine 1864x860)") {
                lifecycle_after_target()
                if (pair == "target")
                    fail("OpenXRay foreground drawable between audio interruption begin and end")
                if (lifecycle_state != 3)
                    fail("foreground drawable before app activate")
                lifecycle_state = 4
                next
            }
            if (relevant_app_or_drawable($0))
                fail("unexpected relevant lifecycle/app/drawable event")
        }
        /iOS audio: interruption began, suspending sound/ {
            if (pair != "")
                fail("nested audio interruption began before interruption ended")
            if ($0 ~ /wasSuspended=1/) {
                pair = "suspended"
                next
            }
            if ($0 !~ /wasSuspended=0/)
                fail("audio interruption began without wasSuspended=0 or wasSuspended=1")
            if (target_complete)
                fail("multiple target audio interruptions")
            if (lifecycle_state != 0 || lifecycle_groups != expected_lifecycle_groups)
                fail("audio interruption began before exactly ordered lifecycle groups=" expected_lifecycle_groups)
            pair = "target"
            next
        }
        /iOS audio: interruption ended \(/ {
            if (pair == "")
                fail("audio interruption ended without interruption begin")
            if (pair == "suspended") {
                pair = ""
                next
            }
            complete = 1
            pair = ""
            target_complete = 1
            next
        }
        END {
            if (failed)
                exit 1
            if (lifecycle_state != 0) {
                print "FAIL: audio log oracle: truncated lifecycle group" > "/dev/stderr"
                exit 1
            }
            if (pair == "target") {
                print "FAIL: audio log oracle: target foreground audio interruption did not end" > "/dev/stderr"
                exit 1
            }
            if (pair == "suspended") {
                print "FAIL: audio log oracle: suspended audio interruption did not end" > "/dev/stderr"
                exit 1
            }
            if (!complete) {
                print "FAIL: audio log oracle: no ordered wasSuspended=0 interruption begin-to-ended pair" > "/dev/stderr"
                exit 1
            }
        }
    ' "$log_path"
}
