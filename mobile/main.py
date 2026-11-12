import flet as ft
import traceback
import sys

# Define main with global error handling
def main(page: ft.Page):
    try:
        page.title = "University Portal Pro"
        page.theme_mode = ft.ThemeMode.DARK
        page.padding = 0
        page.window_width = 375
        page.window_height = 812

        # Safe Imports (Import inside main to catch import errors)
        from services.scraper_cms import MobileCMSScraper
        from services.scraper_lms import MobileLMSScraper
        from services.background_worker import BackgroundWorker
        import threading

        # --- State ---
        cms_scraper = MobileCMSScraper()
        lms_scraper = None 
        
        # --- Background Worker Setup ---
        def get_creds():
            return (page.client_storage.get("username"), page.client_storage.get("password"))
        
        def send_notification(msg):
            page.snack_bar = ft.SnackBar(ft.Text(msg))
            page.snack_bar.open = True
            page.update()

        # Initialize worker carefully
        worker = BackgroundWorker(get_creds, send_notification)

        # --- Views ---
        def login_view():
            username = ft.TextField(label="Enrollment ID", prefix_icon=ft.icons.PERSON)
            password = ft.TextField(label="Password", password=True, can_reveal_password=True, prefix_icon=ft.icons.LOCK)
            error_text = ft.Text("", color="red")
            loading = ft.ProgressRing(visible=False)
            
            def handle_login(e):
                try:
                    loading.visible = True
                    error_text.value = "Connecting..."
                    page.update()
                    
                    success = cms_scraper.login(username.value, password.value)
                    
                    loading.visible = False
                    if success:
                        page.client_storage.set("username", username.value)
                        page.client_storage.set("password", password.value)
                        
                        try:
                            worker.start()
                        except Exception as we:
                            print(f"Worker Error: {we}")
                        
                        page.go("/dashboard")
                    else:
                        error_text.value = "Login Failed"
                    page.update()
                except Exception as ex:
                    loading.visible = False
                    error_text.value = f"Error: {str(ex)}"
                    page.update()

            return ft.View(
                "/",
                [
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Icon(ft.icons.SCHOOL, size=100, color=ft.colors.BLUE_400),
                                ft.Text("Student Portal", size=30, weight=ft.FontWeight.BOLD),
                                ft.Container(height=30),
                                username,
                                password,
                                ft.ElevatedButton("Login", on_click=handle_login, width=300, height=50),
                                loading,
                                error_text
                            ],
                            alignment=ft.MainAxisAlignment.CENTER,
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        padding=30,
                        alignment=ft.alignment.center,
                        expand=True
                    )
                ]
            )

        def dashboard_view():
            username = page.client_storage.get("username")
            
            tabs = ft.Tabs(
                selected_index=0,
                animation_duration=300,
                tabs=[
                    ft.Tab(text="CMS", icon=ft.icons.GRID_VIEW),
                    ft.Tab(text="LMS", icon=ft.icons.BOOK),
                ],
                expand=True
            )

            cms_body = ft.Column(scroll=ft.ScrollMode.HIDDEN, expand=True)
            lms_body = ft.Column(scroll=ft.ScrollMode.HIDDEN, expand=True)

            def load_cms():
                cms_body.controls = [ft.ProgressRing()]
                page.update()
                
                def _task():
                    try:
                        data = cms_scraper.scrape_summary()
                        if data:
                            cms_body.controls.clear()
                            cms_body.controls.append(ft.Text(f"Welcome, {data['name']}", size=20, weight="bold"))
                            cms_body.controls.append(ft.Text(f"Updated: {data['last_updated']}", size=12, color="grey"))
                            
                            for c in data['attendance']:
                                color = ft.colors.GREEN_400
                                if c['absences'] > 2: color = ft.colors.ORANGE_400
                                if c['absences'] > 6: color = ft.colors.RED_400
                                
                                cms_body.controls.append(
                                    ft.Card(
                                        content=ft.Container(
                                            content=ft.Column([
                                                ft.Text(c['course'], weight="bold"),
                                                ft.Row([
                                                    ft.Text(f"{c['percentage']}%", size=22, color=color),
                                                    ft.Text(f"Absences: {c['absences']}")
                                                ], alignment="spaceBetween")
                                            ]),
                                            padding=15
                                        )
                                    )
                                )
                    except Exception as e:
                        cms_body.controls.append(ft.Text(f"Error loading CMS: {e}", color="red"))
                    page.update()
                threading.Thread(target=_task).start()

            def load_lms():
                lms_body.controls = [ft.ProgressRing()]
                page.update()
                
                def _task():
                    try:
                        nonlocal lms_scraper
                        if not lms_scraper:
                            lms_scraper = MobileLMSScraper(cms_scraper.session)
                        
                        connected = lms_scraper.connect_to_lms()
                        lms_body.controls.clear()
                        
                        if connected:
                            lms_body.controls.append(ft.Text("Recent Assignments", weight="bold"))
                            assigns = lms_scraper.get_assignments()
                            for a in assigns:
                                lms_body.controls.append(
                                    ft.Card(
                                        content=ft.ListTile(
                                            leading=ft.Icon(ft.icons.ASSIGNMENT),
                                            title=ft.Text(a['title']),
                                            subtitle=ft.Text(f"{a['course']} • {a['due_date']}"),
                                            trailing=ft.IconButton(ft.icons.DOWNLOAD, 
                                                on_click=lambda e, url=a['download_url']: page.launch_url(url) if url else None)
                                        )
                                    )
                                )
                        else:
                            lms_body.controls.append(ft.Text("Failed to connect to LMS", color="red"))
                    except Exception as e:
                        lms_body.controls.append(ft.Text(f"Error loading LMS: {e}", color="red"))
                    page.update()
                threading.Thread(target=_task).start()

            def on_tab_change(e):
                if e.control.selected_index == 0:
                    load_cms()
                    tabs.content = cms_body
                else:
                    load_lms()
                    tabs.content = lms_body
                page.update()

            tabs.on_change = on_tab_change
            tabs.content = cms_body
            load_cms()

            return ft.View(
                "/dashboard",
                [
                    ft.AppBar(
                        title=ft.Text("University Portal"),
                        actions=[
                            ft.IconButton(ft.icons.REFRESH, on_click=lambda e: load_cms() if tabs.selected_index == 0 else load_lms()),
                            ft.IconButton(ft.icons.LOGOUT, on_click=lambda e: page.go("/"))
                        ]
                    ),
                    tabs
                ]
            )

            # Route Change logic within try block
        def route_change(route):
            page.views.clear()
            if page.route == "/":
                page.views.append(login_view())
            elif page.route == "/dashboard":
                page.views.append(dashboard_view())
            page.update()

        page.on_route_change = route_change
        page.go("/")

    except Exception as e:
        # Catastrophic Global Error Handler
        # This will show the error on the screen instead of crashing silently
        page.clean()
        page.add(
            ft.Column([
                ft.Text("Application Error", size=30, weight="bold", color="red"),
                ft.Text("Please report this to the developer:", size=16),
                ft.Container(height=20),
                ft.Text(str(e), color="red", weight="bold"),
                ft.Container(height=20),
                ft.Text(traceback.format_exc(), size=10, font_family="monospace")
            ], scroll=ft.ScrollMode.ALWAYS, expand=True)
        )
        page.update()

ft.app(target=main)
