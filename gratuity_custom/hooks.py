app_name = "gratuity_custom"
app_title = "Gratuity Custom"
app_publisher = "zainulabdin"
app_description = "Custom Gratuity and Payroll Enhancements"
app_email = "zainulabdin1220@gmail.com"
app_license = "mit"

# Apps
# ------------------

required_apps = ["frappe/erpnext", "frappe/hrms"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "gratuity_custom",
# 		"logo": "/assets/gratuity_custom/logo.png",
# 		"title": "Gratuity Custom",
# 		"route": "/gratuity_custom",
# 		"has_permission": "gratuity_custom.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/gratuity_custom/css/gratuity_custom.css"
# app_include_js = "/assets/gratuity_custom/js/gratuity_custom.js"

# include js, css files in header of web template
# web_include_css = "/assets/gratuity_custom/css/gratuity_custom.css"
# web_include_js = "/assets/gratuity_custom/js/gratuity_custom.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "gratuity_custom/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {"Company": "public/js/company.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "gratuity_custom/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "gratuity_custom.utils.jinja_methods",
# 	"filters": "gratuity_custom.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "gratuity_custom.install.before_install"
after_install = "gratuity_custom.setup.install.after_install"
after_migrate = "gratuity_custom.setup.install.after_migrate"

# Uninstallation
# ------------

before_uninstall = "gratuity_custom.setup.install.before_uninstall"
# after_uninstall = "gratuity_custom.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "gratuity_custom.utils.before_app_install"
# after_app_install = "gratuity_custom.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "gratuity_custom.utils.before_app_uninstall"
# after_app_uninstall = "gratuity_custom.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "gratuity_custom.notifications.get_notification_config"

# Awesome Bar
# -----------
# Extra search results: list of dicts with label, description, route, index.
# route: ["List", "ToDo"], "/desk/docs/some/page", or "https://example.com"
# awesomebar_search = ["gratuity_custom.search.awesomebar_results"]

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Company": {
		"validate": "gratuity_custom.events.company.validate",
	},
	"Gratuity": {
		"validate": "gratuity_custom.events.gratuity.validate",
		"on_submit": "gratuity_custom.events.gratuity.on_submit",
		"on_cancel": "gratuity_custom.events.gratuity.on_cancel",
	},
	"Journal Entry": {
		"validate": "gratuity_custom.events.journal_entry.validate",
		"before_cancel": "gratuity_custom.events.journal_entry.before_cancel",
	},
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"gratuity_custom.tasks.all"
# 	],
# 	"daily": [
# 		"gratuity_custom.tasks.daily"
# 	],
# 	"hourly": [
# 		"gratuity_custom.tasks.hourly"
# 	],
# 	"weekly": [
# 		"gratuity_custom.tasks.weekly"
# 	],
# 	"monthly": [
# 		"gratuity_custom.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "gratuity_custom.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "gratuity_custom.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "gratuity_custom.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["gratuity_custom.utils.before_request"]
# after_request = ["gratuity_custom.utils.after_request"]

# Job Events
# ----------
# before_job = ["gratuity_custom.utils.before_job"]
# after_job = ["gratuity_custom.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"gratuity_custom.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
