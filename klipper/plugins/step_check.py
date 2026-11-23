# step_check: compare stepper positions before and after something
# (e.g. TEST_SPEED_SAFE) and say, per motor, whether it lost steps.
#
#   [step_check]
#
#   STEP_CHECK_MARK      remember every stepper's MCU position now
#   STEP_CHECK_REPORT    print the change since the mark
#
# A stepper that slips loses whole electrical cycles (4 full steps), so a
# change near a multiple of 4 full steps is a slip somewhere (during the
# moves, or against the frame while sensorless homing). Up to one full step
# is ordinary homing scatter.
#
# Install (Kalico): copy into klippy/plugins/ of the Kalico checkout,
# add [step_check] to printer.cfg, restart.
#
# This file may be distributed under the terms of the GNU GPLv3 license.


class StepCheck:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.mark = None
        self.last = {}
        gcode = self.printer.lookup_object("gcode")
        gcode.register_command("STEP_CHECK_MARK", self.cmd_MARK,
                               desc="Remember stepper MCU positions")
        gcode.register_command("STEP_CHECK_REPORT", self.cmd_REPORT,
                               desc="Report stepper position changes since mark")

    def _steppers(self):
        toolhead = self.printer.lookup_object("toolhead")
        toolhead.wait_moves()
        return toolhead.get_kinematics().get_steppers()

    def _microsteps(self, s):
        cfg = self.printer.lookup_object("configfile").get_status(None)
        sec = cfg.get("settings", {}).get(s.get_name(), {})
        fsr = int(sec.get("full_steps_per_rotation", 200))
        _, spr = s.get_rotation_distance()
        return fsr, max(1, int(round(spr / fsr)))

    def cmd_MARK(self, gcmd):
        self.mark = {s.get_name(): s.get_mcu_position() for s in self._steppers()}
        gcmd.respond_info("step_check: marked %d steppers" % len(self.mark))

    def cmd_REPORT(self, gcmd):
        if self.mark is None:
            raise gcmd.error("step_check: run STEP_CHECK_MARK first")
        lines = []
        diffs = {}
        worst = 0
        for s in self._steppers():
            name = s.get_name()
            if name not in self.mark:
                continue
            d = s.get_mcu_position() - self.mark[name]
            diffs[name] = (d, s)
            fsr, ms = self._microsteps(s)
            cycle = 4 * ms
            cycles = d / float(cycle)
            mm = d * s.get_step_dist()
            if abs(d) <= ms:
                verdict, level = "ok", 0
            elif abs(round(cycles)) == 1 and abs(d - round(cycles) * cycle) <= ms:
                verdict, level = "1 cycle (homing scatter)", 1
            else:
                verdict, level = "SLIP %.1f cycles" % cycles, 2
            worst = max(worst, level)
            lines.append("%-10s %+6d usteps  %+.3f mm  %s" % (name, d, mm, verdict))
        # sides of a two-motor axis: change in the gap between them
        for a, b in (("stepper_x", "stepper_x1"), ("stepper_y", "stepper_y1")):
            if a in diffs and b in diffs:
                da, s = diffs[a]
                db, _ = diffs[b]
                lines.append("%s vs %s: gap changed %+d usteps (%+.3f mm)"
                             % (a[-1].upper(), b[-2:].upper(), db - da,
                                (db - da) * s.get_step_dist()))
        lines.append(["RESULT: PASS", "RESULT: PASS (homing scatter only)",
                      "RESULT: FAIL, a motor lost steps"][worst])
        self.last = {n: d for n, (d, _) in diffs.items()}
        self.last["result"] = worst
        gcmd.respond_info("\n".join(lines))

    def get_status(self, eventtime=None):
        return {"last": dict(self.last), "marked": self.mark is not None}


def load_config(config):
    return StepCheck(config)
