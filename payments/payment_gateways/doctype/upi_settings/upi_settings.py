# Copyright (c) 2024, Frappe Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import call_hook_method
from frappe.integrations.utils import create_request_log
from payments.utils import create_payment_gateway

import requests, json


class UPISettings(Document):
	supported_currencies = ["INR"]

	push_txs_url = "https://iciciapi.lyra-network.in/erpservice/ERP/PushTxn"
	check_status_url = "https://iciciapi.lyra-network.in/erpservice/ERP/CheckStatus"
	callback_check_status_url = "https://iciciapi.lyra-network.in/erpservice/ERP/CallbackStatusCheck"


	def	validate(self):
		create_payment_gateway("UPI")
		call_hook_method("payment_gateway_enabled", gateway="UPI")
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
		api_url = 'https://iciciapi.lyra-network.in/erpservice/ERP/CheckStatus'

		with requests.Session() as s:
			s.headers = {
				'content-type': 'application/json'
				}

			r = s.post(api_url, data=json.dumps(data))
			#frappe.throw(str(r.json()))

			if r.json().get('ResponseCode') == '01':
				return "OK"
			else:
				#frappe.msgprint(r.json().get('ResponseDesc'))
				return r.json().get('ResponseDesc')
				#r.raise_for_status()


@frappe.whitelist()
def icici_check_status():
	icici_controller = frappe.get_doc("UPI Settings")

	# Sample Data to check service availability
	data = {
		"mid": icici_controller.mid,
		"tid": icici_controller.tid,
		"tran_type": 1,
		"bill_no": "123456",
		"erp_tran_id": "240230025530444",
		"erp_client_id": icici_controller.erp_client_id,
		"source_id": icici_controller.source_id
	}

	return icici_controller.checkStatus(data)


def pushTxn():
	pass


def cancelTxn():
	pass


def callback():
	pass


def CheckCallbackStatus():
	pass