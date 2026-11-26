import argparse

from gui import scan_controller


def print_timings(title, rows):
    if rows:
        print(f"\n{title}")
        print(f"{'Meting':<44} {'Mediaan':>10} {'Maximum':>10} {'Aantal':>6}")
        for row in rows:
            median = f"{row['mediaan']:.5g}" if row["mediaan"] is not None else "—"
            maximum = f"{row['maximum']:.5g}" if row["maximum"] is not None else "—"
            print(f"{row['meting']:<44} {median:>10} {maximum:>10} {row['n']:>6}")


def main():
    parser = argparse.ArgumentParser(description="Meet DAC-timing of voer een cavity-scan uit.")
    parser.add_argument("--timing", action="store_true", help="Voer alleen de timingtest uit.")
    parser.add_argument("--timing-code", type=int, default=scan_controller.DEFAULT_TIMING_CODE,
                        help="Referentiecode voor de DAC-timingtest.")
    parser.add_argument("--step", type=int, default=scan_controller.DEFAULT_SCAN_STEP,
                        help="DAC-stapgrootte voor de timingtest en scan.")
    parser.add_argument("--sim", "--simulatie", action="store_true")
    parser.add_argument("--PDACon", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.timing_code <= 4095:
        parser.error("--timing-code moet tussen 1 en 4095 liggen.")
    if not 1 <= args.step <= 4095:
        parser.error("--step moet tussen 1 en 4095 liggen.")

    if args.timing:
        scan_controller.measure_timing(args.timing_code, step=args.step)
    else:
        scan_controller.scan_cavity(step=args.step, timing_code=args.timing_code)

    if scan_controller.SIMULATIE:
        print("Simulatie")
    status = scan_controller.get_status()
    print(status["status"])
    print_timings("Timingtest", status["timing"]["benchmark"])
    print_timings("Scan", status["timing"]["scan"])
    if status["error"]:
        raise SystemExit(status["error"])

    if not args.timing:
        import matplotlib.pyplot as plt

        codes, detector, feedback = zip(*scan_controller.get_points(include_feedback=True))
        _, detector_axis = plt.subplots()
        feedback_axis = detector_axis.twinx()
        unit = status["y_unit"]
        detector_axis.set(xlabel="DAC waarde", ylabel=f"Kanaal 1 (detector, {unit})")
        feedback_axis.set(ylabel=f"Kanaal 2 (DAC, {unit})")
        detector_axis.plot(codes, detector)
        feedback_axis.plot(codes, feedback, color="tab:green")
        plt.show()


if __name__ == "__main__":
    main()
