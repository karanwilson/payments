# Copyright (c) 2024, Frappe Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import call_hook_method
from frappe.integrations.utils import create_request_log
from payments.utils import create_payment_gateway

import requests, json
import asyncio


class UPISettings(Document):
	supported_currencies = ["INR"]

	check_status_url = "https://iciciapi.lyra-network.in/erpservice/ERP/CheckStatus"
	push_txs_url = "https://iciciapi.lyra-network.in/erpservice/ERP/PushTxn"
	cancel_txs_url = 'https://iciciapi.lyra-network.in/erpservice/ERP/CancelTxn'
	callback_check_status_url = "https://iciciapi.lyra-network.in/erpservice/ERP/CallbackStatusCheck"


	def	validate(self):
		create_payment_gateway("UPI")
		call_hook_method("payment_gateway_enabled", gateway="UPI")

		self.erp_callback_api_token = self.erp_callback_api_key + ':' + self.get_password(fieldname="erp_callback_api_secret", raise_exception=False)
		#if not self.flags.ignore_mandatory:
		#	self.validate_upi_credentials()

	def	validate_upi_credentials(self):
		pass

	def	validate_transaction_currency(self, currency):
		if currency not in self.supported_currencies:
			frappe.throw(
				_(
					"Please select another payment method. FS does not support transaction in currency '{0}'"
				).format(currency)
			)

	def checkStatus(self, data):
		with requests.Session() as s:
			s.headers = {
				'content-type': 'application/json',
				'User-Agent': 'Custom App'
				}

			r = s.post(self.check_status_url, data=json.dumps(data))
			#frappe.throw(str(r.json()))

			if r.json().get('ResponseCode') == '01':
				return "OK"
			else:
				#frappe.msgprint(r.json().get('ResponseDesc'))
				return r.json().get('ResponseDesc')
				#r.raise_for_status()


	def pushTxn(self, data):
		with requests.Session() as s:
			s.headers = {
				'content-type': 'application/json',
				'User-Agent': 'Custom App'
				}
			
			r = s.post(self.push_txs_url, data=json.dumps(data), timeout=40)
			#frappe.throw(str(r.json()))

			if r.json().get('ResponseCode') != '00':
				frappe.msgprint(r.json().get('ResponseDesc'))

			return r.json()
			#r.raise_for_status()


	def cancelTxn(self, data):
		with requests.Session() as s:
			s.headers = {
				'content-type': 'application/json',
				'User-Agent': 'Custom App'
				}

			r = s.post(self.cs, data=json.dumps(data))
			#frappe.throw(str(r.json()))

			if r.json().get('ResponseCode') == '00':
				return "OK"
			else:
				#frappe.msgprint(r.json().get('ResponseDesc'))
				return r.json().get('ResponseDesc')
				#r.raise_for_status()


@frappe.whitelist()
def icici_check_service():
	icici_controller = frappe.get_doc("UPI Settings")

	# Sample Data to check service availability
	data = {
		"mid": icici_controller.mid,
		"tid": icici_controller.tid,
		"tran_type": 16,
		"bill_no": "123456",
		"erp_tran_id": "240230025530444",
		"erp_client_id": icici_controller.erp_client_id,
		"source_id": icici_controller.source_id
	}

	return icici_controller.checkStatus(data)


@frappe.whitelist()
async def get_upi_confirmation(invoice_doc=None):

	if invoice_doc:
		invoice_dict = json.loads(invoice_doc)
		integration_request_existing = frappe.get_value("Integration Request", {"reference_docname": invoice_dict["name"]}, "name")
		integration_request = frappe.get_doc("Integration Request", integration_request_existing)

		webhook_req_log_list = frappe.get_list("Webhook Request Log", {})

		for webhook_req_log in webhook_req_log_list:
			if webhook_req_log:
				#
				integration_request.status == "Completed"

				return

	else:
		data = await asyncio.wait(frappe.request.data, timeout=40)

		if data:
			icici_controller = frappe.get_doc("UPI Settings")

			webhook_req_log = frappe.new_doc("Webhook Request Log")
			webhook_req_log.user = icici_controller.erp_callback_user_id
			if invoice_dict:
				webhook_req_log.reference_document = invoice_dict.get('name')
			else:
				webhook_req_log.reference_document = data.get('billNumber')
			webhook_req_log.response = data.json()
			webhook_req_log.insert()

	if invoice_doc:
		# flow comes here when POS awaits for fresh callback, and needs to update txn status
		if data.get("TxnStatus") == "SUCCESS" and data.get(''):
			integration_request.status == "Completed"


@frappe.whitelist()
def icici_push_txn(invoice_doc, tran_type, amount, tip):
	invoice_dict = json.loads(invoice_doc)

	integration_request = None

	integration_request_existing = frappe.get_value("Integration Request", {"reference_docname": invoice_dict["name"]}, "name")
	if integration_request_existing:
		integration_request = frappe.get_doc("Integration Request", integration_request_existing)
		if integration_request.status == "Completed":
			#data = json.loads(integration_request.data)
			remarks = _("Integration Request: {0}").format(integration_request.name)
			return {
				"custom_upi_transfer_status": "Success",
				"remarks": remarks
			}

	payment_dict = {
		'reference_doctype': invoice_dict["doctype"],
		'reference_docname': invoice_dict["name"],
		"Customer Name": invoice_dict["customer_name"],
		"Customer ID": invoice_dict["customer"],
		"tran_type": tran_type,
		"amount": amount,
		"tip": tip,
		# String format example: PTDC/EXTRA.CON/PAY-2024-00859/CLSQ524OS7
		# string[0:5] extracts the first 5 chars of the string
		"check": "Yes",
	}

	if not integration_request_existing:
		# Create integration log
		integration_request = create_request_log(payment_dict, service_name="UPI")
	else:
		integration_request.data = json.dumps(payment_dict)

	icici_controller = frappe.get_doc("UPI Settings")

	data = {
		"mid": icici_controller.mid,
		"tid": icici_controller.tid,
		"tran_type": tran_type,
		"amount": amount,
		"bill_no": invoice_dict["name"],
		"tip": tip,
		"erp_tran_id": integration_request.name,
		"erp_client_id": icici_controller.erp_client_id,
		"source_id": icici_controller.source_id
	}

	res = icici_controller.pushTxn(data)

	if res.get("ResponseCode") == "00" or res.get("ResponseDesc") == "Success":
		integration_request.status = "Queued"
	else:
		integration_request.status = "Failed"

	integration_request.save(ignore_permissions=True)
	frappe.db.commit()
	return {
		"ResponseCode": res.get("ResponseCode"),
		"ResponseDesc": res.get("ResponseDesc")
	}


def icici_cancel_txn():
	pass


def callback():
	pass


def CheckCallbackStatus():
	pass