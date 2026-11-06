import os
import csv
import random
from datetime import datetime

MODULE_ID = "rfccurp"
MODULE_NAME = "🪖 RFC & CURP"
MODULE_VERSION = "1.0.0"
MODULE_AUTHOR = "OLIMPO"
MODULE_DATA_SCOPE = "per_user"

COST_PER_ENTRY = 25

DELIVERY_FORMAT = """🪖REPORTE DE IDENTIDAD🪖

Sujeto:
  👤 Nombre: {nombre_completo}

  🎂 Edad: {edad}
  ⚧ Sexo: {sexo}
  📅 Fecha Nac: {fecha_nacimiento}
  🌍 Estado Nac: {estado_nacimiento}

Datos:
  🆔 CURP: {curp}
  💵 RFC: {rfc}
  🗳 C. Elector: No disponible
  📄 Folio INE: No disponible

Dirección:
  🏛 Estado: No disponible
  🏙 Munic: No disponible
  🏘 Col: No disponible
  📮 CP: No disponible
  📍 Calle: No disponible
  🏡 Número: No disponible
🪖 Cambio y fuera 🪖"""

def on_activar():
    sdk.db_create_table(
        f"{MODULE_ID}_entregas",
        {
            "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
            "user_id": "INTEGER NOT NULL",
            "timestamp": "DATETIME NOT NULL",
            "csv_file": "TEXT",
            "record_index": "INTEGER",
            "rfc": "TEXT",
            "curp": "TEXT",
            "credits_charged": "INTEGER",
            "status": "TEXT"
        }
    )
    
    sdk.db_create_table(
        f"{MODULE_ID}_config",
        {
            "key": "TEXT PRIMARY KEY",
            "value": "TEXT"
        }
    )
    
    sdk.db_insert(f"{MODULE_ID}_config", {"key": "cost_per_entry", "value": str(COST_PER_ENTRY)})
    sdk.db_insert(f"{MODULE_ID}_config", {"key": "last_updated", "value": str(datetime.now())})
    
    sdk.log(f"Módulo {MODULE_NAME} activado")

def get_config_value(key, default=None):
    result = sdk.db_select(f"{MODULE_ID}_config", where=f"key = '{key}'")
    if result:
        return result[0]['value']
    return default

def set_config_value(key, value):
    existing = sdk.db_select(f"{MODULE_ID}_config", where=f"key = '{key}'")
    if existing:
        sdk.db_update(f"{MODULE_ID}_config", {"value": str(value)}, where=f"key = '{key}'")
    else:
        sdk.db_insert(f"{MODULE_ID}_config", {"key": key, "value": str(value)})

def load_csv_file(csv_path):
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"El archivo CSV no existe: {csv_path}")
    
    datos = []
    with open(csv_path, 'r', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            datos.append(row)
    
    if not datos:
        raise ValueError("El archivo CSV está vacío")
    
    return datos

def get_random_record(csv_path):
    datos = load_csv_file(csv_path)
    
    if not datos:
        raise ValueError("No hay datos disponibles")
    
    record = random.choice(datos)
    
    record.setdefault('Nombre', 'No disponible')
    record.setdefault('Apellido_Paterno', 'No disponible')
    record.setdefault('Apellido_Materno', 'No disponible')
    
    if record.get('Nombre_Completo'):
        nombre_completo = record['Nombre_Completo']
    else:
        nombre_completo = f"{record.get('Nombre', '')} {record.get('Apellido_Paterno', '')} {record.get('Apellido_Materno', '')}".strip()
    
    record['Nombre_Completo'] = nombre_completo if nombre_completo else 'No disponible'
    record['RFC'] = record.get('RFC', 'No disponible')
    record['CURP'] = record.get('CURP', 'No disponible')
    record['Fecha_Nacimiento'] = record.get('Fecha_Nacimiento', 'No disponible')
    record['Estado_Nacimiento'] = record.get('Estado_Nacimiento', 'No disponible')
    record['Edad_Actual'] = record.get('Edad_Actual', 'No disponible')
    record['Sexo'] = record.get('Sexo', 'No disponible')
    
    return record

def format_delivery_message(record):
    return DELIVERY_FORMAT.format(
        nombre_completo=record.get('Nombre_Completo', 'No disponible'),
        edad=record.get('Edad_Actual', 'No disponible'),
        sexo=record.get('Sexo', 'No disponible'),
        fecha_nacimiento=record.get('Fecha_Nacimiento', 'No disponible'),
        estado_nacimiento=record.get('Estado_Nacimiento', 'No disponible'),
        curp=record.get('CURP', 'No disponible'),
        rfc=record.get('RFC', 'No disponible')
    )

def render(user_id):
    user_credits = sdk.get_user_credits(user_id)
    cost = int(get_config_value("cost_per_entry", COST_PER_ENTRY))
    
    message = f"""📂 *{MODULE_NAME}*

Para obtener una identidad, necesito que me proporciones el archivo CSV generado por el módulo pdfcurp.

*💰 Costo por entrada:* {cost} créditos
*📊 Tus créditos:* {user_credits} créditos

Por favor, envía el archivo CSV que contiene los datos procesados."""
    
    sdk.send_message(user_id, message)
    
    csv_file = sdk.wait_for_file(user_id, timeout=300)
    
    if not csv_file:
        sdk.send_message(user_id, "⏰ Tiempo de espera agotado. Operación cancelada.")
        return
    
    if not csv_file.lower().endswith('.csv'):
        sdk.send_message(user_id, "❌ Error: El archivo debe ser un CSV (.csv)")
        return
    
    if user_credits < cost:
        sdk.send_message(user_id, f"❌ Créditos insuficientes. Necesitas {cost} créditos, tienes {user_credits}.")
        return
    
    transaction_id = sdk.charge_credits(user_id, cost, f"{MODULE_ID}_entrada")
    
    try:
        sdk.db_insert(f"{MODULE_ID}_entregas", {
            "user_id": user_id,
            "timestamp": str(datetime.now()),
            "csv_file": csv_file,
            "record_index": -1,
            "credits_charged": cost,
            "status": "pending"
        })
        
        record = get_random_record(csv_file)
        delivery_message = format_delivery_message(record)
        sdk.send_message(user_id, delivery_message)
        
        last_id = sdk.db_select(f"{MODULE_ID}_entregas", where=f"user_id = {user_id} ORDER BY id DESC LIMIT 1")[0]['id']
        sdk.db_update(f"{MODULE_ID}_entregas", {
            "rfc": record.get('RFC', ''),
            "curp": record.get('CURP', ''),
            "status": "delivered",
            "record_index": "random"
        }, where=f"id = {last_id}")
        
        sdk.log(f"Entregada identidad a usuario {user_id}")
        
    except FileNotFoundError as e:
        sdk.refund_credits(user_id, cost, transaction_id, f"Error: {str(e)}")
        sdk.send_message(user_id, f"❌ Error: {str(e)}")
        
    except ValueError as e:
        sdk.refund_credits(user_id, cost, transaction_id, f"Error: {str(e)}")
        sdk.send_message(user_id, f"❌ Error: {str(e)}")
        
    except Exception as e:
        sdk.refund_credits(user_id, cost, transaction_id, f"Error inesperado: {str(e)}")
        sdk.send_message(user_id, "❌ Error inesperado al procesar el CSV. Créditos reembolsados.")
        sdk.log(f"Error en entrega para usuario {user_id}: {str(e)}")

def render_admin(user_id):
    if not sdk.is_admin(user_id):
        sdk.send_message(user_id, "❌ No tienes permisos de administrador.")
        return
    
    current_cost = get_config_value("cost_per_entry", COST_PER_ENTRY)
    
    message = f"""⚙️ *Panel de Administración - {MODULE_NAME}*

*Configuración actual:*
• Costo por entrada: {current_cost} créditos

*Opciones disponibles:*
1. Cambiar costo por entrada
2. Ver estadísticas
3. Exportar registros

Selecciona una opción (1-3):"""
    
    sdk.send_message(user_id, message)
    option = sdk.wait_for_response(user_id, timeout=60)
    
    if option == "1":
        sdk.send_message(user_id, f"💰 Costo actual: {current_cost} créditos\n\nIngresa el nuevo costo (número entero):")
        new_cost = sdk.wait_for_response(user_id, timeout=60)
        
        try:
            new_cost_int = int(new_cost)
            if new_cost_int <= 0:
                sdk.send_message(user_id, "❌ El costo debe ser mayor a 0.")
                return
            
            set_config_value("cost_per_entry", new_cost_int)
            sdk.send_message(user_id, f"✅ Costo actualizado a {new_cost_int} créditos por entrada.")
            sdk.log(f"Admin {user_id} cambió costo a {new_cost_int} créditos")
            
        except ValueError:
            sdk.send_message(user_id, "❌ Error: Debes ingresar un número válido.")
            
    elif option == "2":
        total_entregas = sdk.db_select(f"{MODULE_ID}_entregas", count=True)
        entregas_exitosas = sdk.db_select(f"{MODULE_ID}_entregas", count=True, where="status = 'delivered'")
        ingresos_totales = sdk.db_select(f"{MODULE_ID}_entregas", select="SUM(credits_charged) as total", where="status = 'delivered'")
        
        stats_message = f"""📊 *Estadísticas del Módulo*

• Total de entregas solicitadas: {total_entregas or 0}
• Entregas exitosas: {entregas_exitosas or 0}
• Ingresos totales: {ingresos_totales[0]['total'] if ingresos_totales else 0} créditos
• Costo actual por entrada: {current_cost} créditos"""
        
        sdk.send_message(user_id, stats_message)
        
    elif option == "3":
        registros = sdk.db_select(f"{MODULE_ID}_entregas", limit=1000)
        
        if not registros:
            sdk.send_message(user_id, "📭 No hay registros para exportar.")
            return
        
        import tempfile
        temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8')
        
        try:
            import csv as csv_module
            fieldnames = ['id', 'user_id', 'timestamp', 'csv_file', 'rfc', 'curp', 'credits_charged', 'status']
            
            writer = csv_module.DictWriter(temp_file, fieldnames=fieldnames)
            writer.writeheader()
            
            for registro in registros:
                writer.writerow(registro)
            
            temp_file.close()
            
            sdk.send_file(user_id, temp_file.name, caption="📋 Registros de entregas")
            
        except Exception as e:
            sdk.send_message(user_id, f"❌ Error al exportar registros: {str(e)}")
        finally:
            try:
                os.unlink(temp_file.name)
            except:
                pass
    else:
        sdk.send_message(user_id, "❌ Opción no válida. Operación cancelada.")

def deliver_entry(user_id, csv_file_path):
    cost = int(get_config_value("cost_per_entry", COST_PER_ENTRY))
    
    user_credits = sdk.get_user_credits(user_id)
    if user_credits < cost:
        return False, f"Créditos insuficientes. Necesitas {cost}, tienes {user_credits}."
    
    transaction_id = sdk.charge_credits(user_id, cost, f"{MODULE_ID}_auto_entrada")
    
    try:
        record = get_random_record(csv_file_path)
        
        sdk.db_insert(f"{MODULE_ID}_entregas", {
            "user_id": user_id,
            "timestamp": str(datetime.now()),
            "csv_file": csv_file_path,
            "record_index": -1,
            "rfc": record.get('RFC', ''),
            "curp": record.get('CURP', ''),
            "credits_charged": cost,
            "status": "delivered"
        })
        
        delivery_message = format_delivery_message(record)
        sdk.send_message(user_id, delivery_message)
        
        return True, f"Entrada entregada exitosamente. Costo: {cost} créditos."
        
    except Exception as e:
        sdk.refund_credits(user_id, cost, transaction_id, f"Error: {str(e)}")
        return False, f"Error al entregar entrada: {str(e)}"

def on_desactivar():
    sdk.log(f"Módulo {MODULE_NAME} desactivado")
