import matplotlib.pyplot as plt

from gui import scan_controller


STEP = 50
DELAY = 0.001
AANTAL_BLOKKEN = 1


def main():
    scan_controller.scan_cavity(step=STEP, delay=DELAY, aantal_blokken=AANTAL_BLOKKEN)
    status = scan_controller.get_status()
    if status["error"]:
        raise SystemExit(status["error"])
    print(status["status"])

    punten = scan_controller.get_points(include_feedback=True)
    if not punten:
        return

    codes, detector, feedback = zip(*punten)
    figure, detector_axis = plt.subplots()
    feedback_axis = detector_axis.twinx()
    unit = f' ({status["y_unit"]})' if status["y_unit"] else ""
    detector_axis.set(xlabel="DAC waarde", ylabel=f"Kanaal 1 (detector){unit}")
    feedback_axis.set_ylabel(f"Kanaal 2 (DAC){unit}")
    detector_axis.plot(codes, detector)
    feedback_axis.plot(codes, feedback, color="tab:green")
    figure.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
