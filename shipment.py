# This file is part of the carrier_send_shipments module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.pool import Pool, PoolMeta
from trytond.transaction import Transaction
from seur.picking import Picking
from trytond.modules.carrier_send_shipments.tools import unaccent, unspaces
from trytond.modules.carrier_send_shipments_seur.tools import set_seur_reference, \
    seurbarcode
from base64 import decodestring
import os
import logging
import tempfile
import genshi

__all__ = ['ShipmentOut']
__metaclass__ = PoolMeta

logger = logging.getLogger(__name__)
offline_loader = genshi.template.TemplateLoader(
    os.path.join(os.path.dirname(__file__), 'template'),
    auto_reload=True)


class ShipmentOut:
    __name__ = 'stock.shipment.out'

    @classmethod
    def __setup__(cls):
        super(ShipmentOut, cls).__setup__()
        cls._error_messages.update({
            'seur_add_services': 'Select a service or default service in Seur API',
            'seur_not_country': 'Add country in shipment "%(name)s" delivery address',
            'seur_error_zip': 'Seur not accept zip "%(zip)s"',
            'seur_not_send': 'Not send shipment "%(name)s"',
            'seur_not_send_error': 'Not send shipment "%(name)s". %(error)s',
            'seur_not_label': 'Not available "%(name)s" label from Seur',
            'seur_reference_int': 'Seur reference is not an integer. Please, '
                'check the seur sequence',
            })

    @staticmethod
    def seur_picking_data(api, shipment, service, price=None, weight=False):
        '''
        Seur Picking Data
        :param api: obj
        :param shipment: obj
        :param service: str
        :param price: string
        :param weight: bol
        Return data
        '''
        pool = Pool()
        Uom = pool.get('product.uom')
        Date = pool.get('ir.date')
        SeurZip = pool.get('carrier.api.seur.zip')

        if api.reference_origin and hasattr(shipment, 'origin'):
            code = shipment.origin and shipment.origin.rec_name or shipment.code
        else:
            code = shipment.code

        notes = ''
        if shipment.carrier_notes:
            notes = '%s\n' % unaccent(shipment.carrier_notes)

        packages = shipment.number_packages
        if not packages or packages == 0:
            packages = 1

        if shipment.warehouse.address:
            waddress = shipment.warehouse.address
        else:
            waddress = api.company.party.addresses[0]

        warehouse_street = unaccent(waddress.street)
        warehouse_city = unaccent(waddress.city)
        warehouse_zip = unaccent(waddress.zip)
        warehouse_country_code = waddress.country.code if waddress.country else None

        customer_name = unaccent(shipment.delivery_address.name
            or shipment.customer.name)
        customer_street = unaccent(shipment.delivery_address.street)
        customer_city = unaccent(shipment.delivery_address.city)
        customer_zip = unaccent(shipment.delivery_address.zip)
        customer_country_code = shipment.delivery_address.country.code

        codpos_zips = set()
        if warehouse_zip:
            codpos_zips.add(warehouse_zip)
        if customer_zip:
            if customer_country_code in ['PT']:
                customer_zip = customer_zip.replace('-','')
            codpos_zips.add(customer_zip)
        codpos_countries = set()
        if warehouse_country_code:
            codpos_countries.add(warehouse_country_code)
        if customer_country_code:
            codpos_countries.add(customer_country_code)

        seur_zips = dict(((z.codpos_zip, z.codpos_country), z) for z in SeurZip.search([
            ('codpos_zip', 'in', list(codpos_zips)),
            ('codpos_country', 'in', list(codpos_countries)),
            ('coddest_name', '!=', None),
            ]))

        notes = '%(notes)s' \
            '%(name)s. %(street)s. %(zip)s %(city)s - %(country)s\n' % {
                'notes': notes,
                'name': customer_name,
                'street': customer_street,
                'zip': customer_zip,
                'city': customer_city,
                'country': customer_country_code,
                }

        data = {}
        data['date'] = Date.today().strftime('%d/%m/%y')
        data['company_name'] = unaccent(api.company.party.name)
        data['company_street'] = warehouse_street

        seur_company_zip = warehouse_zip
        seur_company_city = warehouse_city
        if api.seur_offline and seur_zips.get((warehouse_zip, warehouse_country_code)):
            seur_zip = seur_zips[(warehouse_zip, warehouse_country_code)]
            seur_company_zip = seur_zip.codpos_code
            seur_company_city = seur_zip.codpos_city
        data['company_zip'] = seur_company_zip
        data['company_city'] = seur_company_city

        data['servicio'] = str(service.code)
        data['total_bultos'] = packages
        data['observaciones'] = notes
        data['referencia_expedicion'] = code
        data['ref_bulto'] = code
        data['clave_portes'] = 'F'
        if shipment.carrier_cashondelivery and price:
            data['clave_reembolso'] = 'F' # F: Facturacion
            data['valor_reembolso'] = str(price)
        else:
            data['clave_reembolso'] = ' '
            data['valor_reembolso'] = '0'

        sweight = 1.0
        if weight and hasattr(shipment, 'weight_func'):
            sweight = shipment.weight_func
            if sweight == 0 or sweight == 0.0:
                sweight = 1.0
            if api.weight_api_unit:
                if shipment.weight_uom:
                    sweight = Uom.compute_qty(
                        shipment.weight_uom, weight, api.weight_api_unit)
                elif api.weight_unit:
                    sweight = Uom.compute_qty(
                        api.weight_unit, weight, api.weight_api_unit)

        data['total_kilos'] = str(sweight)
        data['peso_bulto'] = str(sweight)

        data['cliente_nombre'] = customer_name
        data['cliente_direccion'] = customer_street
        #~ data['cliente_tipovia'] = 'CL'
        #~ data['cliente_tnumvia'] = 'N'
        #~ data['cliente_numvia'] = '93'
        #~ data['cliente_escalera'] = 'A'
        #~ data['cliente_piso'] = '3'
        #~ data['cliente_puerta'] = '2'

        seur_customer_zip = customer_zip
        # seur_customer_city = customer_city
        seur_coddest_name = customer_city
        if api.seur_offline and seur_zips.get((customer_zip, customer_country_code)):
            seur_zip = seur_zips[(customer_zip, customer_country_code)]
            seur_customer_zip = seur_zip.codpos_code
            # seur_customer_city = unaccent(seur_zip.codpos_city)
            seur_coddest_name = unaccent(seur_zip.coddest_name)

        data['cliente_cpostal'] = customer_zip
        data['cliente_poblacion'] = customer_city
        data['cliente_pais'] = customer_country_code
        # offline
        data['seur_coddest_name'] = seur_coddest_name

        # Service code 77 means international
        if service.code == '77':
            data['seur_codpos_code'] = '890'
            data['product'] = '70'
            data['product_short_name'] = 'INTE'
            data['service_short_name'] = '*CLSC'
        else:
            data['seur_codpos_code'] = seur_customer_zip
            data['product'] = '2'
            data['product_short_name'] = 'ESTD'
            data['service_short_name'] = '*B2C'

        data['cliente_email'] = unspaces(shipment.email)
        data['cliente_telefono'] = unspaces(shipment.phone)
        data['sms_consignatario'] = unspaces(shipment.mobile)
        data['cliente_atencion'] = customer_name
        data['aviso_preaviso'] = 'S' if api.seur_aviso_preaviso else 'N'
        data['aviso_reparto'] = 'S' if api.seur_aviso_reparto else 'N'
        data['aviso_email'] = 'S' if api.seur_aviso_email else 'N'
        data['aviso_sms'] = 'S' if api.seur_aviso_sms else 'N'
        data['id_mercancia'] = '400' # TODO fixed ID mercancia
        return data

    @classmethod
    def send_seur(cls, api, shipments):
        'Send shipments out to seur'
        if api.seur_offline:
            return cls.send_seur_offline(api, shipments)
        else:
            return cls.send_seur_api(api, shipments)

    @classmethod
    def send_seur_api(cls, api, shipments):
        'Send shipments out to seur'
        CarrierApi = Pool().get('carrier.api')

        references = []
        labels = []
        errors = []

        default_service = CarrierApi.get_default_carrier_service(api)
        dbname = Transaction().database.name

        seur_context = {}
        if api.seur_pdf:
            seur_context['pdf'] = True
        with Picking(api.username, api.password, api.vat, api.seur_franchise, api.seur_seurid, \
                api.seur_ci, api.seur_ccc, timeout=api.timeout, context=seur_context) as picking_api:
            for shipment in shipments:
                service = shipment.carrier_service or shipment.carrier.service or default_service
                if not service:
                    message = cls.raise_user_error('seur_add_services', {},
                        raise_exception=False)
                    errors.append(message)
                    continue

                if not shipment.delivery_address.country:
                    message = cls.raise_user_error('seur_not_country', {},
                        raise_exception=False)
                    errors.append(message)
                    continue

                price = None
                if shipment.carrier_cashondelivery:
                    price = shipment.carrier_cashondelivery_price

                data = cls.seur_picking_data(api, shipment, service, price, api.weight)
                # Send shipment data to carrier
                logger.info('Send SEUR API data: %s' % data)
                reference, label, error = picking_api.create(data)

                if reference:
                    cls.write([shipment], {
                        'carrier_tracking_ref': reference,
                        'carrier_service': service,
                        'carrier_delivery': True,
                        'carrier_printed': True,
                        'carrier_send_date': cls.get_carrier_date(),
                        'carrier_send_employee': cls.get_carrier_employee(),
                        })
                    logger.info('Send shipment %s' % (shipment.code))
                    references.append(shipment.code)
                else:
                    logger.error('Not send shipment %s.' % (shipment.code))

                if label:
                    if api.seur_pdf:
                        with tempfile.NamedTemporaryFile(
                                prefix='%s-seur-%s-' % (dbname, reference),
                                suffix='.pdf', delete=False) as temp:
                            temp.write(decodestring(label))
                    else:
                        with tempfile.NamedTemporaryFile(
                                prefix='%s-seur-%s-' % (dbname, reference),
                                suffix='.zpl', delete=False) as temp:
                            temp.write(label.encode('utf-8'))
                    logger.info('Generated tmp label %s' % (temp.name))
                    temp.close()
                    labels.append(temp.name)
                else:
                    message = cls.raise_user_error('seur_not_label', {
                            'name': shipment.rec_name,
                            }, raise_exception=False)
                    errors.append(message)
                    logger.error(message)

                if error:
                    message = cls.raise_user_error('seur_not_send_error', {
                            'name': shipment.rec_name,
                            'error': error,
                            }, raise_exception=False)
                    logger.error(message)
                    errors.append(message)

        return references, labels, errors

    @classmethod
    def send_seur_offline(cls, api, shipments):
        'Send Seur Offline'
        pool = Pool()
        SeurOffline = pool.get('carrier.api.seur.offline')
        Sequence = pool.get('ir.sequence')
        CarrierApi = pool.get('carrier.api')

        # XML data will be created when send Seur email

        tmpl = offline_loader.load('offline-label.zpl',
            cls=genshi.template.text.NewTextTemplate)

        dbname = Transaction().database.name
        min_ref = api.seur_minimum_reference
        max_ref = api.seur_maximun_reference
        default_service = CarrierApi.get_default_carrier_service(api)
        sequence_id = api.seur_reference.id

        references = []
        labels = []
        errors = []

        to_create = []
        to_write = []
        for shipment in shipments:
            price = None
            if shipment.carrier_cashondelivery:
                price = shipment.carrier_cashondelivery_price

            service = shipment.carrier_service or shipment.carrier.service \
                or default_service

            vals = cls.seur_picking_data(api, shipment, service, price, api.weight)

            if vals['clave_portes'] == 'D':
                vals['clave_portes'] = 'P.Debidos'
            else:
                # clave_protes == F
                vals['clave_portes'] = 'P.Pagados'
            if vals['clave_reembolso'] == 'F':
                vals['clave_reembolso'] = 'R'

            seur_references = []
            for i in range(0, vals['total_bultos']):
                try:
                    reference = int(Sequence.get_id(sequence_id))
                except:
                    cls.raise_user_error('seur_reference_int')
                seur_reference = str(set_seur_reference(min_ref, max_ref, reference))
                seur_references.append(seur_reference)

                barcode = seurbarcode(
                    from_zip=shipment.warehouse.address.zip,
                    to_zip=vals['seur_codpos_code'],
                    reference=seur_reference,
                    transport=1) # TODO transport type is fixed to 1
                vals['barcode'] = barcode
                vals['barcode_compact'] = barcode.replace (' ', '')
                vals['bulto'] = i + 1

                zpl = tmpl.generate(**vals).render()
                with tempfile.NamedTemporaryFile(
                        prefix='%s-seur-%s-' % (dbname, seur_reference),
                        suffix='.zpl', delete=False) as temp:
                    temp.write(zpl.encode('utf-8'))

                logger.info(
                    'Generated tmp label %s' % (temp.name))
                labels.append(temp.name)
                temp.close()

            to_create.append({
                'api': api,
                'shipment': shipment,
                'state': 'draft',
                })

            to_write.extend(([shipment], {
                'carrier_tracking_ref': ','.join(seur_references),
                'carrier_delivery': True,
                'carrier_printed': True,
                'carrier_send_date': cls.get_carrier_date(),
                'carrier_send_employee': cls.get_carrier_employee(),
                }))
            references.extend(seur_references)

        if to_write:
            cls.write(*to_write)
        if to_create:
            with Transaction().set_user(0):
                SeurOffline.create(to_create)

        return references, labels, errors

    @classmethod
    def print_labels_seur(cls, api, shipments):
        'Print Seur Labels'
        if api.seur_offline:
            return cls.print_labels_seur_offline(api, shipments)
        else:
            return cls.print_labels_seur_api(api, shipments)

    @classmethod
    def print_labels_seur_api(cls, api, shipments):
        '''
        Get Seur labels from Shipment Out
        '''
        CarrierApi = Pool().get('carrier.api')

        default_service = CarrierApi.get_default_carrier_service(api)
        dbname = Transaction().database.name

        labels = []
        errors = []

        seur_context = {}
        if api.seur_pdf:
            seur_context['pdf'] = True
        with Picking(api.username, api.password, api.vat, api.seur_franchise, api.seur_seurid, \
                api.seur_ci, api.seur_ccc, timeout=api.timeout, context=seur_context) as picking_api:
            for shipment in shipments:
                service = shipment.carrier_service or default_service
                if not service:
                    message = 'Add %s service or configure a default API Seur service.' % (shipment.code)
                    errors.append(message)
                    logger.error(message)
                    continue

                if not shipment.delivery_address.country:
                    message = 'Add %s a country.' % (shipment.code)
                    errors.append(message)
                    logger.error(message)
                    continue

                price = None
                if shipment.carrier_cashondelivery:
                    price = shipment.carrier_cashondelivery_price

                data = cls.seur_picking_data(api, shipment, service, price, api.weight)
                label = picking_api.label(data)

                if label:
                    if api.seur_pdf:
                        with tempfile.NamedTemporaryFile(
                                prefix='%s-seur-%s-' % (dbname, shipment.carrier_tracking_ref),
                                suffix='.pdf', delete=False) as temp:
                            temp.write(decodestring(label.encode('utf-8')))
                    else:
                        with tempfile.NamedTemporaryFile(
                                prefix='%s-seur-%s-' % (dbname, shipment.carrier_tracking_ref),
                                suffix='.zpl', delete=False) as temp:
                            temp.write(label.encode('utf-8'))
                    logger.info(
                        'Generated tmp label %s' % (temp.name))
                    temp.close()
                    labels.append(temp.name)
                else:
                    message = 'Not label %s shipment available from Seur.' % (shipment.code)
                    errors.append(message)
                    logger.error(message)

        return labels

    @classmethod
    def print_labels_seur_offline(cls, api, shipments):
        'Print Label Seur Offline'
        CarrierApi = Pool().get('carrier.api')

        tmpl = offline_loader.load('offline-label.zpl',
            cls=genshi.template.text.NewTextTemplate)

        dbname = Transaction().database.name
        default_service = CarrierApi.get_default_carrier_service(api)

        labels = []
        for shipment in shipments:
            from_zip = shipment.warehouse.address.zip

            price = None
            if shipment.carrier_cashondelivery:
                price = shipment.carrier_cashondelivery_price

            service = shipment.carrier_service or shipment.carrier.service \
                or default_service

            vals = cls.seur_picking_data(api, shipment, service, price, api.weight)

            if vals['clave_portes'] == 'D':
                vals['clave_portes'] = 'P.Debidos'
            else:
                # clave_protes == F
                vals['clave_portes'] = 'P.Pagados'
            if vals['clave_reembolso'] == 'F':
                vals['clave_reembolso'] = 'R'

            bulto = 1
            for seur_reference in shipment.carrier_tracking_ref.split(','):
                barcode = seurbarcode(
                    from_zip=from_zip,
                    to_zip=vals['seur_codpos_code'],
                    reference=seur_reference,
                    transport=1) # TODO transport type is fixed to 1
                vals['barcode'] = barcode
                vals['barcode_compact'] = barcode.replace (' ', '')
                vals['bulto'] = bulto
                bulto += 1

                zpl = tmpl.generate(**vals).render()
                with tempfile.NamedTemporaryFile(
                        prefix='%s-seur-%s-' % (dbname, seur_reference),
                        suffix='.zpl', delete=False) as temp:
                    temp.write(zpl.encode('utf-8'))

                logger.info(
                    'Generated tmp label %s' % (temp.name))
                labels.append(temp.name)
                temp.close()

        return labels
