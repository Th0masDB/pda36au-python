from nicegui import ui, run
import scan_controller
import pi_control
from config import APP_VERSION

def menu():
    with ui.header().classes("items-center px-6 bg-white outline outline-black/5"):
        ui.label("Cavity controller").classes("text-xl text-black font-bold")

        ui.space()

        ui.button(
            "Home",
            icon="home",
            on_click=lambda: ui.navigate.to("/")
        ).props("flat").classes("text-black")

        ui.button(
            "Scan",
            icon="compare",
            on_click=lambda: ui.navigate.to("/scan")
        ).props("flat").classes("text-black")

        ui.button(
            "Grafieken",
            icon="show_chart",
            on_click=lambda: ui.navigate.to("/grafieken")
        ).props("flat").classes("text-black")


        if pi_control.get_pi_model():
            ui.button(
                "Raspberry Pi", icon="settings_power",
                on_click=lambda: ui.navigate.to("/raspberry-pi"),
            ).props("flat").classes("text-black")

@ui.page("/raspberry-pi")
def raspberry_pi():
    model = pi_control.get_pi_model()
    if not model:
        ui.navigate.to("/")
        return

    ui.page_title('Raspberry Pi | Cavity controller')
    menu()
    with ui.column().classes("p-6 w-full gap-4"):
        ui.label("Raspberry Pi-beheer").classes("text-3xl font-bold")
        ui.label(model)
        ui.label("Hiermee sluit je de Raspberry Pi af of herstart je de Pi waarop deze webinterface draait.")
        ui.label("Stop eerst de scan en de live meting. Wacht na afsluiten tot de Pi "
                 "volledig uit is voordat je de voeding loskoppelt.")
        status_label = ui.label("")

        def request_power_action(action):
            # Houd de meetlock vast zodat geen andere browser tijdens het
            # afsluitverzoek een nieuwe meting kan starten.
            with scan_controller.lock:
                if scan_controller.running or scan_controller.live_running:
                    raise RuntimeError("Stop eerst de scan en de live PDA-meting.")
                action()

        async def confirm_power_action(restart=False):
            dialog.close()
            reboot_dialog.close()
            shutdown_button.disable()
            reboot_button.disable()
            operation = "Herstarten" if restart else "Afsluiten"
            status_label.set_text(f"{operation} aanvragen...")
            try:
                await run.io_bound(request_power_action,
                                   pi_control.reboot if restart else pi_control.shutdown)
            except Exception as exc:
                status_label.set_text(f"{operation} mislukt: {exc}")
                shutdown_button.enable()
                reboot_button.enable()
            else:
                status_label.set_text(
                    "De Raspberry Pi herstart. Open de webinterface opnieuw zodra de Pi is opgestart."
                    if restart else
                    "De Raspberry Pi sluit af. De webinterface wordt onbereikbaar."
                )

        with ui.dialog() as dialog, ui.card():
            ui.label("Raspberry Pi afsluiten?").classes("text-xl font-bold")
            ui.label("De webinterface wordt afgesloten. Om opnieuw te starten moet je de Pi weer inschakelen.")
            with ui.row():
                ui.button("Annuleren", on_click=dialog.close).props("flat")
                ui.button("Afsluiten", icon="power_settings_new",
                          color="negative", on_click=lambda: confirm_power_action())

        with ui.dialog() as reboot_dialog, ui.card():
            ui.label("Raspberry Pi herstarten?").classes("text-xl font-bold")
            ui.label("De webinterface wordt tijdelijk onbereikbaar. "
                     "Met de systemd-service start deze na de herstart automatisch weer.")
            with ui.row():
                ui.button("Annuleren", on_click=reboot_dialog.close).props("flat")
                ui.button("Herstarten", icon="restart_alt",
                          on_click=lambda: confirm_power_action(restart=True))

        shutdown_button = ui.button(
            "Raspberry Pi afsluiten", icon="power_settings_new",
            color="negative", on_click=dialog.open,
        )
        reboot_button = ui.button(
            "Raspberry Pi herstarten", icon="restart_alt", on_click=reboot_dialog.open,
        )


@ui.page("/")
def home():
    ui.page_title('Cavity controller')
    menu()

    with ui.column().classes("p-6"):
        ui.label("Home").classes("text-3xl font-bold")
        ui.label("Welkom bij de demo!")

    ui.label(f"Versie {APP_VERSION}").classes(
        "fixed bottom-4 right-4 text-sm text-gray-500"
    )


@ui.page("/scan")
def metingen():
    ui.page_title('Cavity controller')
    menu()

    with ui.column().classes("p-6 w-full gap-4"):
        ui.label("Scan").classes("text-3xl font-bold")

        # Instellingen en bediening
        with ui.row().classes("gap-4 items-center"):
            step_input = ui.number(
                label="DAC stapgrootte", value=50, min=1, max=4095, step=1,
            ).classes("w-44")
            delay_input = ui.number(
                label="Delay per stap (ms)", value=1, min=0, step=0.1,
            ).classes("w-44")
            blocks_input = ui.number(
                label="PDA blokken per punt", value=1, min=1, max=100, step=1,
            ).classes("w-44")
            live_plot = ui.switch("Live grafiek", value=False)

        with ui.row().classes("gap-2"):
            start_button = ui.button("Start scan", icon="play_arrow")
            stop_button = ui.button("Stop", icon="stop")
            stop_button.disable()

        status_label = ui.label("Status: Gereed").classes("font-medium")
        points_label = ui.label("Meetpunten: 0")
        error_label = ui.label("").classes("text-red-600")

        chart = ui.echart({
            "animation": False,
            "tooltip": {"trigger": "axis"},
            "grid": {"left": 70, "right": 30, "top": 30, "bottom": 60},
            "xAxis": {
                "type": "value", "name": "DAC waarde",
                "nameLocation": "middle", "nameGap": 35, "min": 0, "max": 4095,
            },
            "yAxis": {
                "type": "value", "name": "Detectorsignaal",
                "nameLocation": "middle", "nameGap": 50, "scale": True,
            },
            "series": [{
                "name": "PDA36AU", "type": "line", "showSymbol": False, "data": [],
            }],
        }).classes("w-full").style("height: 500px")
        grafiek_status = {"laatste_aantal": 0}

        async def start_scan():
            huidige_status = scan_controller.get_status()
            if huidige_status["running"]:
                return
            if scan_controller.get_live_data()["running"]:
                ui.notify("Stop eerst de live PDA-meting op Grafieken.", type="warning")
                return

            step = max(1, min(4095, int(step_input.value or 50)))
            delay = max(0.0, float(delay_input.value or 0) / 1000.0)
            aantal_blokken = max(1, int(blocks_input.value or 1))

            chart.options["series"][0]["data"] = []
            chart.update()
            grafiek_status["laatste_aantal"] = 0
            start_button.disable()
            stop_button.enable()
            status_label.set_text("Status: Scannen...")
            error_label.set_text("")

            try:
                # Hardware uitlezen in een thread houdt de GUI beschikbaar.
                await run.io_bound(
                    scan_controller.scan_cavity, step, delay, aantal_blokken,
                )
            finally:
                # Na afloop altijd de volledige grafiek tonen.
                punten = scan_controller.get_points()
                chart.options["series"][0]["data"] = punten
                chart.update()
                grafiek_status["laatste_aantal"] = len(punten)
                start_button.enable()
                stop_button.disable()

        def stop_scan():
            scan_controller.stop_scan()
            status_label.set_text("Status: stoppen...")
            stop_button.disable()

        start_button.on("click", start_scan)
        stop_button.on("click", stop_scan)

        def update_gui():
            data = scan_controller.get_status()
            status_label.set_text(f'Status: {data["status"]}')
            points_label.set_text(f'Meetpunten: {data["aantal_punten"]}')
            error_label.set_text(data["error"])

            if not live_plot.value:
                return
            if not data["running"]:
                return
            if data["aantal_punten"] == grafiek_status["laatste_aantal"]:
                return

            # Meetpunten alleen ophalen wanneer de live grafiek verandert.
            punten = scan_controller.get_points()
            chart.options["series"][0]["data"] = punten
            chart.update()
            grafiek_status["laatste_aantal"] = len(punten)

        ui.timer(0.5, update_gui)


@ui.page("/grafieken")
def grafieken():
    ui.page_title('Live PDA | Cavity controller')
    menu()

    with ui.column().classes("p-6 w-full gap-4"):
        ui.label("Live PDA-signaal").classes("text-3xl font-bold")
        ui.label("Het nieuwste meetblok van de detector. Tijdens een scan verschijnt hier automatisch de PDA-data.")
        ui.label("De meting blijft actief bij navigeren. Stop de live meting voordat je een scan start.")
        with ui.row().classes("gap-2"):
            start_button = ui.button("Start live meting", icon="play_arrow",
                                     on_click=scan_controller.start_live)
            stop_button = ui.button("Stop live meting", icon="stop",
                                    on_click=scan_controller.stop_live)
        status_label = ui.label("Gereed")
        error_label = ui.label("").classes("text-red-600")
        chart = ui.echart({
            "animation": False,
            "tooltip": {"trigger": "axis"},
            "grid": {"left": 85, "right": 35, "top": 40, "bottom": 65},
            "xAxis": {"type": "value", "name": "Sample", "nameLocation": "middle", "nameGap": 35},
            "yAxis": {"type": "value", "name": "Detectorsignaal", "scale": True},
            "series": [{"name": "PDA36AU", "type": "line", "showSymbol": False, "data": []}],
        }).classes("w-full").style("height: 500px")
        last_sequence = -1

        def update_live():
            nonlocal last_sequence
            data = scan_controller.get_live_data()
            status_label.set_text(data["status"])
            error_label.set_text(data["error"])
            start_button.set_enabled(not (data["running"] or data["scanning"]))
            stop_button.set_enabled(data["running"])
            if data["sequence"] == last_sequence:
                return
            last_sequence = data["sequence"]
            chart.options["series"][0]["data"] = data["data"]
            chart.options["xAxis"]["name"] = f'Tijd / positie ({data["x_unit"]})'
            chart.options["yAxis"]["name"] = f'Detectorsignaal ({data["y_unit"]})'
            chart.update()

        update_live()
        ui.timer(0.2, update_live)


ui.run()
