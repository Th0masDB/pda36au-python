from nicegui import ui


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
    menu()

    with ui.column().classes("p-6"):
        ui.label("Scan").classes("text-3xl font-bold")



@ui.page("/grafieken")
def grafieken():
    menu()

    with ui.column().classes("p-6"):
        ui.label("Grafieken").classes("text-3xl font-bold")


ui.run()