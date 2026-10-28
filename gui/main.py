from nicegui import ui, run
import scan_controller

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


@ui.page("/")
def home():
    ui.page_title('Cavity controller')
    menu()

    with ui.column().classes("p-6"):
        ui.label("Home").classes("text-3xl font-bold")
        ui.label("Welkom bij de demo!")


@ui.page("/scan")
def metingen():
    ui.page_title('Cavity controller')
    menu()

    with ui.column().classes(
        "p-6 w-full gap-4"
    ):

        ui.label(
            "Scan"
        ).classes(
            "text-3xl font-bold"
        )
        
        # Instellingen

        with ui.row().classes(
            "gap-4 items-center"
        ):

            step_input = ui.number(
                label="DAC stapgrootte",
                value=50,
                min=1,
                max=4095,
                step=1,
            ).classes(
                "w-44"
            )

            delay_input = ui.number(
                label="Delay per stap (ms)",
                value=1,
                min=0,
                step=0.1,
            ).classes(
                "w-44"
            )

            blocks_input = ui.number(
                label="PDA blokken per punt",
                value=1,
                min=1,
                max=100,
                step=1,
            ).classes(
                "w-44"
            )

            live_plot = ui.switch(
                "Live grafiek",
                value=False,
            )
        # Knoppen
        
        with ui.row().classes(
            "gap-2"
        ):

            start_button = ui.button(
                "Start scan",
                icon="play_arrow",
            )

            stop_button = ui.button(
                "Stop",
                icon="stop",
            )

            stop_button.disable()
        
        # Status

        status_label = ui.label(
            "Status: Gereed"
        ).classes(
            "font-medium"
        )

        points_label = ui.label(
            "Meetpunten: 0"
        )

        error_label = ui.label(
            ""
        ).classes(
            "text-red-600"
        )
        
        # Grafiek

        chart = ui.echart({

            # Animaties kosten onnodig tijd tijdens
            # het live bijwerken.
            "animation": False,

            "tooltip": {
                "trigger": "axis",
            },

            "grid": {
                "left": 70,
                "right": 30,
                "top": 30,
                "bottom": 60,
            },

            "xAxis": {
                "type": "value",
                "name": "DAC waarde",
                "nameLocation": "middle",
                "nameGap": 35,
                "min": 0,
                "max": 4095,
            },

            "yAxis": {
                "type": "value",
                "name": "Detectorsignaal",
                "nameLocation": "middle",
                "nameGap": 50,

                # Automatisch schalen rond
                # de gemeten waarden
                "scale": True,
            },

            "series": [{
                "name": "PDA36AU",
                "type": "line",

                # Punten niet afzonderlijk tekenen.
                # Dit is sneller bij veel meetpunten.
                "showSymbol": False,

                "data": [],
            }],

        }).classes(
            "w-full"
        ).style(
            "height: 500px"
        )


        
        # Lokale GUI-status
        

        grafiek_status = {
            "laatste_aantal": 0,
        }


        
        # Scan starten
        

        async def start_scan():

            # Niet opnieuw starten wanneer er
            # al een scan draait.
            huidige_status = (
                scan_controller.get_status()
            )

            if huidige_status["running"]:
                return


            # ---------------------------------------------
            # Instellingen lezen
            # ---------------------------------------------

            step = max(
                1,
                min(
                    4095,
                    int(
                        step_input.value
                        or 50
                    ),
                ),
            )

            delay = max(
                0.0,
                float(
                    delay_input.value
                    or 0
                ) / 1000.0,
            )

            aantal_blokken = max(
                1,
                int(
                    blocks_input.value
                    or 1
                ),
            )


            # ---------------------------------------------
            # Oude grafiek wissen
            # ---------------------------------------------

            chart.options[
                "series"
            ][0][
                "data"
            ] = []

            chart.update()

            grafiek_status[
                "laatste_aantal"
            ] = 0


            # ---------------------------------------------
            # Knoppen
            # ---------------------------------------------

            start_button.disable()
            stop_button.enable()

            status_label.set_text(
                "Status: Scannen..."
            )

            error_label.set_text(
                ""
            )


            # ---------------------------------------------
            # Scan uitvoeren
            # ---------------------------------------------

            try:

                # De hardwaremeting draait in een
                # aparte thread.
                #
                # Daardoor kan NiceGUI ondertussen
                # de live grafiek blijven bijwerken.
                await run.io_bound(
                    scan_controller.scan_cavity,
                    step,
                    delay,
                    aantal_blokken,
                )

            finally:

                # -----------------------------------------
                # Scan klaar:
                # ALTIJD volledige grafiek tonen
                # -----------------------------------------

                punten = (
                    scan_controller.get_points()
                )

                chart.options[
                    "series"
                ][0][
                    "data"
                ] = punten

                chart.update()

                grafiek_status[
                    "laatste_aantal"
                ] = len(punten)


                # Knoppen herstellen
                start_button.enable()
                stop_button.disable()


        
        # Scan stoppen
        

        def stop_scan():

            scan_controller.stop_scan()

            status_label.set_text(
                "Status: stoppen..."
            )

            stop_button.disable()


        # Knoppen koppelen aan functies
        start_button.on(
            "click",
            start_scan,
        )

        stop_button.on(
            "click",
            stop_scan,
        )


        
        # GUI bijwerken
        

        def update_gui():

            # Alleen statusinformatie ophalen.
            #
            # Hierdoor wordt niet iedere halve seconde
            # de volledige lijst met meetpunten gekopieerd.
            data = (
                scan_controller.get_status()
            )


            # ---------------------------------------------
            # Tekst bijwerken
            # ---------------------------------------------

            status_label.set_text(
                f'Status: {data["status"]}'
            )

            points_label.set_text(
                f'Meetpunten: '
                f'{data["aantal_punten"]}'
            )

            error_label.set_text(
                data["error"]
            )


            # ---------------------------------------------
            # Live grafiek
            # ---------------------------------------------

            # Alleen tijdens de scan én wanneer
            # Live grafiek aan staat.
            if not live_plot.value:
                return

            if not data["running"]:
                return


            # Alleen opnieuw tekenen wanneer er
            # daadwerkelijk nieuwe meetpunten zijn.
            if (
                data["aantal_punten"]
                == grafiek_status["laatste_aantal"]
            ):
                return


            # Nu pas de volledige meetpunten ophalen.
            punten = (
                scan_controller.get_points()
            )


            # Grafiek vernieuwen
            chart.options[
                "series"
            ][0][
                "data"
            ] = punten

            chart.update()


            grafiek_status[
                "laatste_aantal"
            ] = len(punten)


        
        # Timer
        

        # Twee keer per seconde de GUI controleren.
        #
        # Live UIT:
        # alleen kleine statusinformatie.
        #
        # Live AAN:
        # maximaal twee grafiekupdates per seconde.
        ui.timer(
            0.5,
            update_gui,
        )



@ui.page("/grafieken")
def grafieken():
    menu()

    with ui.column().classes("p-6"):
        ui.label("Grafieken").classes("text-3xl font-bold")


ui.run()