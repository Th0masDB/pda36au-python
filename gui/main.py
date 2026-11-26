from nicegui import ui, run
from datetime import datetime
import git_control
import scan_controller
import pi_control
from config import APP_VERSION


def sigma_series():
    return [{"name": name, "type": "line", "showSymbol": False,
             "lineStyle": {"type": "dotted", "width": 2},
             "itemStyle": {"color": "#dc2626"}, "data": []}
            for name in ("Baseline + 2σ", "Baseline − 2σ")]


def update_sigma_lines(chart, background, baseline):
    changed = False
    for index, sign in ((2, 1), (3, -1)):
        points = ([[x, y + sign * 2 * background["sigma"]] for x, y in baseline]
                  if background["sigma"] is not None else [])
        changed = changed or chart.options["series"][index]["data"] != points
        chart.options["series"][index]["data"] = points
    return changed


def run_when_idle(action, *args):
    # Voorkom dat een andere browser tijdens de actie een meting start.
    with scan_controller.dac_lock, scan_controller.lock:
        if scan_controller.running or scan_controller.live_running:
            raise RuntimeError("Stop eerst de scan en de live PDA-meting.")
        return action(*args)


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


async def branch_controls():
    with ui.column().classes("w-full gap-4"):
        ui.label("Git-branch wisselen").classes("text-xl font-bold")
        try:
            branch_data = await run.io_bound(git_control.get_branches)
        except RuntimeError as exc:
            ui.label(str(exc)).classes("text-red-600")
            return
        if branch_data is None:
            return

        current, branches = branch_data
        current_label = ui.label(
            f"Huidige Git-branch is {current}" if current else "Losse Git-commit (detached HEAD)"
        )
        if not branches:
            ui.label("Geen Git-branches beschikbaar.")
            return

        ui.label("Dit wisselt de branch op de server. Kies een branch met deze instelling "
                 "als je via de webinterface wilt terugwisselen.")
        with ui.row().classes("gap-4 items-center"):
            branch_select = ui.select(branches, label="Git-branch", with_input=True,
                                      value=current if current in branches else None).classes("w-64")
            switch_button = ui.button("Branch wisselen", icon="swap_horiz")
        status_label = ui.label("").classes("whitespace-pre-wrap")

        def update_switch_button():
            switch_button.set_enabled(branch_select.enabled and branch_select.value not in (None, current))

        async def apply_branch():
            nonlocal current
            if not switch_button.enabled:
                return
            branch = branch_select.value
            branch_select.disable()
            switch_button.disable()
            status_label.set_text("Branch wisselen…")
            try:
                switched = await run.io_bound(run_when_idle, git_control.switch_branch, branch)
            except RuntimeError as exc:
                status_label.set_text(str(exc))
            else:
                if switched is not None:
                    current = switched
                    current_label.set_text(f"Huidige Git-branch is {current}")
                    status_label.set_text("Branch gewijzigd. Herstart de applicatie als die niet automatisch herlaadt.")
            finally:
                branch_select.enable()
                update_switch_button()

        branch_select.on_value_change(update_switch_button)
        switch_button.on("click", apply_branch)
        update_switch_button()


@ui.page("/raspberry-pi")
async def raspberry_pi():
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

        async def confirm_power_action(restart=False):
            dialog.close()
            reboot_dialog.close()
            shutdown_button.disable()
            reboot_button.disable()
            operation = "Herstarten" if restart else "Afsluiten"
            status_label.set_text(f"{operation} aanvragen...")
            try:
                await run.io_bound(run_when_idle,
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


        ui.separator()
        await branch_controls()


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
                label="Extra wachttijd na trigger (ms)", value=0, min=0, step=0.1,
            ).classes("w-60")
            blocks_input = ui.number(
                label="PDA blokken per punt", value=1, min=1, max=100, step=1,
            ).classes("w-44")
            live_plot = ui.switch("Live grafiek", value=True)

        with ui.row().classes("gap-2 items-center"):
            start_button = ui.button("Start scan", icon="play_arrow")
            stop_button = ui.button("Stop", icon="stop")
            stop_button.disable()
            timing_code_input = ui.number(
                "Timingreferentie (DAC-code)", value=scan_controller.DEFAULT_TIMING_CODE,
                min=1, max=4095, step=1,
            ).classes("w-52")
            timing_button = ui.button("Meet timing", icon="timer")

            def export_scan():
                try:
                    content = scan_controller.export_scan_csv()
                except ValueError as exc:
                    ui.notify(str(exc), type="warning")
                    return
                filename = f'cavity_scan_{datetime.now().astimezone():%Y%m%d_%H%M%S}.csv'
                ui.download(content, filename, media_type="text/csv")

            export_button = ui.button("Exporteer CSV", icon="download", on_click=export_scan)
            export_button.disable()

        with ui.expansion("Achtergrondscan", icon="settings", value=False).classes("w-full"):
            ui.label("Dek de laser af en meet de achtergrond met de scaninstellingen hierboven. "
                     "De DAC doorloopt hetzelfde bereik. σ wordt berekend over de gemiddelde scanpunten.")
            with ui.row().classes("gap-2"):
                background_scan_button = ui.button("Meet achtergrondscan", icon="horizontal_rule")
                clear_scan_background_button = ui.button(
                    "Wis scanbaseline", icon="clear", on_click=scan_controller.clear_scan_background)
            scan_background_label = ui.label("Geen achtergrondscan")

        status_label = ui.label("Status: Gereed").classes("font-medium")
        points_label = ui.label("Meetpunten: 0")
        error_label = ui.label("").classes("text-red-600")

        chart = ui.echart({
            "animation": False,
            "tooltip": {"trigger": "axis"},
            "grid": {"left": 80, "right": 45, "top": 45, "bottom": 85,
                     "containLabel": True},
            "legend": {"data": ["Kanaal 1 (detector)", "Kanaal 2 (DAC)", "Baseline", "Baseline + 2σ", "Baseline − 2σ"]},
            "xAxis": {
                "type": "value", "name": "DAC waarde",
                "nameLocation": "middle", "nameGap": 45, "min": 0, "max": 4095,
                "splitNumber": 5, "minInterval": 1,
                "axisLabel": {"fontSize": 13, "color": "#374151", "margin": 12,
                              "hideOverlap": True},
                "nameTextStyle": {"fontSize": 14, "color": "#374151"},
            },
            "yAxis": [{
                "type": "value", "name": "Detectorsignaal",
                "nameLocation": "middle", "nameGap": 50, "scale": True,
            }, {
                "type": "value", "name": "DAC-feedback", "scale": True,
                "splitLine": {"show": False},
            }],
            "series": [{
                "name": "Kanaal 1 (detector)", "type": "line", "showSymbol": False, "data": [],
            }, {
                "name": "Baseline", "type": "line", "showSymbol": False,
                "lineStyle": {"type": "dashed", "width": 2},
                "itemStyle": {"color": "#d97706"}, "data": [],
            }, *sigma_series(), {
                "name": "Kanaal 2 (DAC)", "type": "line", "yAxisIndex": 1, "showSymbol": False,
                "itemStyle": {"color": "#0f766e"}, "data": [],
            }],
        }).classes("w-full").style("height: 500px")
        grafiek_status = {"laatste_aantal": 0}
        timing_columns = [
            {"name": name, "field": name, "label": label, "align": align}
            for name, label, align in (("meting", "Meting", "left"), ("mediaan", "Mediaan", "right"),
                                       ("maximum", "Maximum", "right"), ("n", "Aantal", "right"))
        ]
        timing_tables = {}
        with ui.expansion("Timingresultaten", icon="timer", value=True).classes("w-full"):
            ui.label("De timingtest gebruikt kanaal 2. De hosttijd omvat de I²C-write, PDA-opname en USB-overdracht.")
            if scan_controller.SIMULATIE:
                ui.label("Simulatie").classes("font-medium")
            for key, title in (("benchmark", "Timingtest"), ("scan", "Laatste scan")):
                timing_tables[key] = ui.table(
                    columns=timing_columns, rows=[], row_key="meting", title=title,
                ).props("flat dense").classes("w-full")

        def update_scan_series(rows):
            chart.options["series"][0]["data"] = [[code, detector] for code, detector, _ in rows]
            chart.options["series"][-1]["data"] = [[code, feedback] for code, _, feedback in rows]
            grafiek_status["laatste_aantal"] = len(rows)

        def update_timing_tables(timing):
            for key, table in timing_tables.items():
                rows = [{**row, **{field: f"{row[field]:.5g}" if row[field] is not None else "—"
                                   for field in ("mediaan", "maximum")}}
                        for row in timing[key]]
                if table.rows != rows:
                    table.update_rows(rows)

        def update_scan_baseline(data):
            background = data["background"]
            value = background["value"]
            # Zonder scan gebruiken we de eenheid van de achtergrondmeting.
            unit = data["y_unit"]
            baseline = ([[0, value], [4095, value]]
                        if value is not None and data["background_compatible"] and unit in (None, background["y_unit"])
                        else [])
            changed = chart.options["series"][1]["data"] != baseline
            chart.options["series"][1]["data"] = baseline
            sigma_changed = update_sigma_lines(chart, background, baseline)
            changed = changed or sigma_changed
            display_unit = unit if unit is not None else background["y_unit"]
            axis_name = f"Detectorsignaal ({display_unit})" if display_unit else "Detectorsignaal"
            changed = changed or chart.options["yAxis"][0]["name"] != axis_name
            chart.options["yAxis"][0]["name"] = axis_name
            chart.options["yAxis"][1]["name"] = f"DAC-feedback ({unit})" if unit else "DAC-feedback"
            return changed

        async def start_scan(achtergrond=False, timing_only=False):
            huidige_status = scan_controller.get_status()
            if huidige_status["running"]:
                return
            if huidige_status["live_running"]:
                ui.notify("Stop eerst de live PDA-meting op Grafieken.", type="warning")
                return

            step = max(1, min(4095, int(step_input.value or 50)))
            delay = max(0.0, float(delay_input.value or 0) / 1000.0)
            aantal_blokken = max(1, int(blocks_input.value or 1))
            timing_code = int(timing_code_input.value or scan_controller.DEFAULT_TIMING_CODE)

            if not achtergrond and not timing_only:
                update_scan_series([])
                chart.update()
            start_button.disable()
            timing_button.disable()
            background_scan_button.disable()
            stop_button.enable()
            status_label.set_text("Status: Timing meten...")
            error_label.set_text("")

            try:
                # Hardware uitlezen in een thread houdt de GUI beschikbaar.
                if timing_only:
                    await run.io_bound(scan_controller.measure_timing, timing_code)
                else:
                    await run.io_bound(
                        scan_controller.scan_cavity, step, delay, aantal_blokken, achtergrond, timing_code=timing_code,
                    )
            finally:
                if not chart.is_deleted:
                    update_scan_series(scan_controller.get_points(include_feedback=True))
                    update_gui()
                    chart.update()

        def stop_scan():
            scan_controller.stop_scan()
            status_label.set_text("Status: stoppen...")
            stop_button.disable()

        start_button.on("click", lambda: start_scan())
        timing_button.on("click", lambda: start_scan(timing_only=True))
        background_scan_button.on("click", lambda: start_scan(achtergrond=True))
        stop_button.on("click", stop_scan)

        def update_gui():
            data = scan_controller.get_status()
            status_label.set_text(f'Status: {data["status"]}')
            points_label.set_text(f'Meetpunten: {data["aantal_punten"]}')
            export_button.set_enabled(data["aantal_punten"] > 0)
            busy = data["running"] or data["live_running"]
            start_button.set_enabled(not busy)
            timing_button.set_enabled(not busy)
            timing_code_input.set_enabled(not busy)
            background_scan_button.set_enabled(not busy)
            stop_button.set_enabled(data["running"])
            clear_scan_background_button.set_enabled(not busy and data["background"]["value"] is not None)
            reference = data["background"]
            text = reference["status"] + f' — punten: {reference["points"]}'
            if reference["value"] is not None:
                text += f' — baseline: {reference["value"]:.6g}, σ: {reference["sigma"]:.6g} {reference["y_unit"]}'
                if reference["settings"] != (int(step_input.value or 50),
                                              float(delay_input.value or 0) / 1000,
                                              int(blocks_input.value or 1)):
                    text += " — Instellingen gewijzigd: meet opnieuw de achtergrond vóór de volgende scan."
            scan_background_label.set_text(text)
            error_label.set_text(data["error"])
            update_timing_tables(data["timing"])

            chart_changed = update_scan_baseline(data)
            if ((live_plot.value or not data["running"])
                    and data["aantal_punten"] != grafiek_status["laatste_aantal"]):
                update_scan_series(scan_controller.get_points(include_feedback=True))
                chart_changed = True
            if chart_changed:
                chart.update()

        update_gui()
        ui.timer(0.2, update_gui)


@ui.page("/grafieken")
def grafieken():
    ui.page_title('Live PDA | Cavity controller')
    menu()

    with ui.column().classes("p-6 w-full gap-4"):
        ui.label("Live PDA-signaal").classes("text-3xl font-bold")
        ui.label("Het nieuwste PDA-meetblok. Kanaal 1 toont de detector en kanaal 2 de DAC-uitgang voor timingmetingen.")
        ui.label("De meting blijft actief bij navigeren. Stop de live meting voordat je een scan start.")
        with ui.row().classes("gap-2"):
            start_button = ui.button("Start live meting", icon="play_arrow",
                                     on_click=scan_controller.start_live)
            stop_button = ui.button("Stop live meting", icon="stop",
                                    on_click=scan_controller.stop_live)
        with ui.row().classes("gap-2 items-center"):
            dac_input = ui.number("DAC-waarde (0–4095)", value=0, step=1).classes("w-48")
            dac_button = ui.button("Stel DAC in", icon="tune")
            dac_status = ui.label("")
        setting_dac = False

        async def apply_dac():
            nonlocal setting_dac
            if setting_dac:
                return
            setting_dac = True
            dac_button.disable()
            dac_status.set_text("DAC instellen…")
            try:
                value = await run.io_bound(scan_controller.set_manual_dac, dac_input.value)
            except (RuntimeError, ValueError) as exc:
                dac_status.set_text(str(exc))
            else:
                if value is not None:
                    name = "Gesimuleerde DAC" if scan_controller.SIMULATIE else "DAC"
                    dac_status.set_text(f"{name} ingesteld op {value}.")
            finally:
                setting_dac = False

        dac_button.on("click", apply_dac)
        with ui.expansion("Achtergrondmeting", icon="settings", value=False).classes("w-full"):
            ui.label("Blokkeer de detector en klik op Meet achtergrond. "
                     "De meting stopt automatisch als er geen live meting actief was. "
                     "De baseline is het gemiddelde van de gekozen meetblokken.")
            with ui.row().classes("gap-2 items-center"):
                background_blocks = ui.number("Achtergrondblokken", value=1, min=1,
                                              max=100, step=1).classes("w-44")

                def measure_background():
                    try:
                        started = scan_controller.start_background(background_blocks.value or 1)
                    except (ValueError, TypeError, OverflowError) as exc:
                        ui.notify(str(exc), type="warning")
                        return
                    if not started:
                        ui.notify("Wacht tot de huidige meting of het stoppen daarvan klaar is.",
                                  type="warning")

                background_button = ui.button("Meet achtergrond", icon="horizontal_rule",
                                              on_click=measure_background)
                clear_background_button = ui.button("Wis baseline", icon="clear",
                                                    on_click=scan_controller.clear_background)
            background_label = ui.label("Geen achtergrondmeting")
        status_label = ui.label("Gereed")
        error_label = ui.label("").classes("text-red-600")
        detector_name = "Kanaal 1 (detector)"
        dac_name = "Kanaal 2 (DAC)"
        chart = ui.echart({
            "animation": False,
            "tooltip": {"trigger": "axis"},
            "grid": {"left": 85, "right": 45, "top": 45, "bottom": 85,
                     "containLabel": True},
            "legend": {"data": [detector_name, dac_name, "Baseline", "Baseline + 2σ", "Baseline − 2σ"]},
            "xAxis": {
                "type": "value", "name": "Sample", "nameLocation": "middle", "nameGap": 45,
                "splitNumber": 5,
                "axisLabel": {"fontSize": 13, "color": "#374151", "margin": 12,
                              "hideOverlap": True},
                "nameTextStyle": {"fontSize": 14, "color": "#374151"},
            },
            "yAxis": {"type": "value", "name": "Signaal", "scale": True},
            "series": [
                {"name": detector_name, "type": "line", "showSymbol": False, "data": []},
                {"name": "Baseline", "type": "line", "showSymbol": False,
                 "lineStyle": {"type": "dashed", "width": 2},
                 "itemStyle": {"color": "#d97706"}, "data": []},
                *sigma_series(),
                {"name": dac_name, "type": "line", "showSymbol": False,
                 "itemStyle": {"color": "#0f766e"}, "data": []},
            ],
        }).classes("w-full").style("height: 500px")
        last_sequence = -1
        last_background = None

        def update_live():
            nonlocal last_sequence, last_background
            data = scan_controller.get_live_data()
            status_label.set_text(data["status"])
            error_label.set_text(data["error"])
            start_button.set_enabled(not (data["running"] or data["scanning"]))
            stop_button.set_enabled(data["running"])
            dac_input.set_enabled(not data["scanning"])
            dac_button.set_enabled(not data["scanning"] and not setting_dac)
            background = data["background"]
            background_label.set_text(background["status"] + (
                f' — baseline: {background["value"]:.6g} {background["y_unit"]}'
                if background["value"] is not None else "") + (
                f' — σ: {background["sigma"]:.6g} {background["y_unit"]}'
                if background["sigma"] is not None else ""))
            background_button.set_enabled(not data["scanning"] and not background["target"])
            clear_background_button.set_enabled(background["value"] is not None or bool(background["target"]))
            baseline = (background["value"], background["y_unit"], background["sigma"])
            if data["sequence"] == last_sequence and baseline == last_background:
                return
            last_sequence = data["sequence"]
            last_background = baseline
            chart.options["series"][0]["data"] = data["data"]
            chart.options["series"][-1]["data"] = data["analog_data"]
            chart.options["series"][1]["data"] = (
                [[data["data"][0][0], baseline[0]], [data["data"][-1][0], baseline[0]]]
                if data["data"] and baseline[0] is not None and baseline[1] == data["y_unit"]
                else [])
            update_sigma_lines(chart, background, chart.options["series"][1]["data"])
            chart.options["xAxis"]["name"] = f'Tijd / positie ({data["x_unit"]})'
            chart.options["yAxis"]["name"] = f'Signaal ({data["y_unit"]})' if data["y_unit"] else "Signaal"
            chart.update()

        update_live()
        ui.timer(0.2, update_live)


ui.run()
