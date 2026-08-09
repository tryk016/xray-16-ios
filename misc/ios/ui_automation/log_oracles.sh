#!/usr/bin/env bash

# Shared device-log oracles for the physical XCUITest scenarios.  This file is
# intentionally source-only: callers own shell options, device leases and paths.

verify_lifecycle_oracle()
{
    local log_path="$1"
    awk '
        function fail(message) {
            print "FAIL: lifecycle log oracle: " message > "/dev/stderr"
            failed = 1
            exit 1
        }
        /iOS: app deactivate/ {
            if (state != 0)
                fail("deactivate before the previous group completed")
            state = 1
            next
        }
        /iOS: app activate/ {
            if (state == 1) {
                state = 2
                next
            }
            if (state == 2)
                fail("second activate before foreground drawable")
            next
        }
        /iOS: foreground drawable 1864x860 \(engine 1864x860\)/ {
            if (state != 2)
                next
            ++groups
            state = 0
            if (groups == 5) {
                complete = 1
                exit 0
            }
        }
        END {
            if (failed)
                exit 1
            if (!complete) {
                print "FAIL: lifecycle log oracle: ordered deactivate->activate->drawable groups=" groups > "/dev/stderr"
                exit 1
            }
        }
    ' "$log_path"
}

verify_audio_oracle()
{
    local log_path="$1"
    local minimum_lifecycle_groups="$2"
    awk -v minimum_lifecycle_groups="$minimum_lifecycle_groups" '
        function fail(message) {
            print "FAIL: audio log oracle: " message > "/dev/stderr"
            failed = 1
            exit 1
        }
        /iOS: app deactivate/ {
            if (pair == "target")
                fail("OpenXRay deactivated between audio interruption begin and end")
            if (lifecycle_state == 0)
                lifecycle_state = 1
            else if (lifecycle_state == 2)
                fail("deactivate before the preceding lifecycle group reached foreground drawable")
            else
                fail("deactivate before the previous lifecycle group completed")
            next
        }
        /iOS: app activate/ {
            if (lifecycle_state == 1) {
                lifecycle_state = 2
                next
            }
            if (lifecycle_state == 2)
                fail("second activate before foreground drawable")
            next
        }
        /iOS: foreground drawable 1864x860 \(engine 1864x860\)/ {
            if (lifecycle_state == 2) {
                ++lifecycle_groups
                lifecycle_state = 0
            }
            next
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
            if (lifecycle_groups < minimum_lifecycle_groups)
                fail("audio interruption began before required ordered lifecycle groups=" minimum_lifecycle_groups)
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
            exit 0
        }
        END {
            if (failed)
                exit 1
            if (complete)
                exit 0
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
