"""Generador de tarjetas de crédito válidas por Luhn."""
from __future__ import annotations

import random
import re
from datetime import datetime
from typing import List, Optional


class CardGenerator:
    """Generador compatible con el /gen de Hades V1."""

    MAX_X = 6

    @staticmethod
    def generate_cc(number: str, month: str, year: str, cvv: str, quantity: int = 10) -> List[str]:
        if not re.match(r"^[0-9xX]+$", number):
            raise ValueError("El número solo puede contener dígitos y 'x'")
        if number.count("x") + number.count("X") > CardGenerator.MAX_X:
            raise ValueError("El número máximo de 'x' permitido es 6.")
        if len(number) < 6 or not number[:6].isdigit():
            raise ValueError("Los primeros 6 dígitos deben estar explícitos (sin 'x').")

        brand = CardGenerator._brand_key(number)
        card_length = 15 if brand == "amex" else (14 if brand == "diners" else 16)
        cvv_length = 4 if brand == "amex" else 3

        cards: List[str] = []
        for _ in range(quantity):
            cc_number = CardGenerator._generate_card_number(number, card_length)
            exp_month = CardGenerator._generate_month(month)
            exp_year = CardGenerator._generate_year(year)
            cvv_gen = CardGenerator._generate_cvv(cvv, cvv_length)
            cards.append(f"{cc_number}|{exp_month}|{exp_year}|{cvv_gen}")

        return cards

    @staticmethod
    def _brand_key(number: str) -> str:
        """Marca por prefijo (determinista, sin depender del texto de la base).

        JCB 35, Discover 60/6011/622/64x/65, Amex 34/37 y Maestro se
        reconocen explícito; el bloque de BIN en el mensaje sigue
        saliendo de la base de datos.
        """
        if number[:2] in ("34", "37"):
            return "amex"
        if (
            number[:3] in ("300", "301", "302", "303", "304", "305")
            or number[:2] in ("36", "38", "39")
        ):
            return "diners"
        if number[:2] == "35":
            return "jcb"
        if (
            number[:4] == "6011"
            or number[:2] == "65"
            or number[:3] in ("644", "645", "646", "647", "648", "649")
            or (len(number) >= 6 and 622126 <= int(number[:6]) <= 622925)
        ):
            return "discover"
        if number[:1] == "4":
            return "visa"
        if number[:2] in ("51", "52", "53", "54", "55") or (
            len(number) >= 4 and 2221 <= int(number[:4]) <= 2720
        ):
            return "mastercard"
        if number[:2] in ("50", "56", "57", "58", "59") or number[:1] == "6":
            return "maestro"
        return "other"

    @staticmethod
    def _fill_x(prefix: str) -> str:
        """Reemplaza 'x' evitando rachas obvias (111, 123, 321)."""
        result: List[str] = []
        for char in prefix:
            if char.lower() == "x":
                while True:
                    digit = str(random.randint(0, 9))
                    if len(result) >= 2 and result[-1].isdigit() and result[-2].isdigit():
                        prev1 = int(result[-1])
                        prev2 = int(result[-2])
                        if digit == result[-1] == result[-2]:
                            continue
                        if digit == str(prev1 + 1) and result[-1] == str(prev2 + 1):
                            continue
                        if digit == str(prev1 - 1) and result[-1] == str(prev2 - 1):
                            continue
                    result.append(digit)
                    break
            else:
                result.append(char)
        return "".join(result)

    @staticmethod
    def _generate_card_number(prefix: str, length: int) -> str:
        number = CardGenerator._fill_x(prefix)

        if len(number) < length - 1:
            number += "".join(str(random.randint(0, 9)) for _ in range(length - 1 - len(number)))
        elif len(number) >= length:
            number = number[: length - 1]

        check_digit = CardGenerator._luhn_check_digit(number)
        return number + str(check_digit)

    @staticmethod
    def _luhn_check_digit(number: str) -> int:
        """Dígito de control Luhn para un parcial (sin el dígito final).

        El dígito final quedará a la derecha, así que cada dígito del
        parcial sube una posición: se duplica el de índice par desde
        la derecha (0, 2, 4...).
        """
        total = 0
        for i, ch in enumerate(reversed(number)):
            d = int(ch)
            if i % 2 == 0:
                d *= 2
                if d > 9:
                    d -= 9
            total += d
        return (10 - (total % 10)) % 10

    @staticmethod
    def _generate_month(month: str) -> str:
        if month.lower() in ("rand", "rnd"):
            current_month = datetime.now().month
            return str(random.randint(current_month, 12)).zfill(2)
        return month.zfill(2)

    @staticmethod
    def _generate_year(year: str) -> str:
        if year.lower() in ("rand", "rnd"):
            current_year = datetime.now().year
            return str(random.randint(current_year, current_year + 10))
        return year

    @staticmethod
    def _generate_cvv(cvv: str, length: int) -> str:
        if cvv.lower() in ("rand", "rnd", "xxx", "xxxx"):
            return "".join(str(random.randint(0, 9)) for _ in range(length))
        return cvv.zfill(length)


def validate_gen_input(month: str, year: str, cvv: str) -> bool:
    """Valida mes, año y cvv para /gen. Acepta rnd/rand y rechaza vencidos."""
    try:
        m = month.strip().lower()
        y = str(year).strip()
        if m in ("rnd", "rand"):
            month_int: Optional[int] = None
        else:
            month_int = int(m)
            if month_int < 1 or month_int > 12:
                return False
        if y.lower() in ("rnd", "rand"):
            year_int: Optional[int] = None
        else:
            year_int = int(y)
            if len(y) == 2:
                year_int += 2000
            if year_int < 2000 or year_int > 2100:
                return False
        if month_int is not None and year_int is not None:
            now = datetime.now()
            if (year_int, month_int) < (now.year, now.month):
                return False
        if cvv.lower() not in ("rnd", "xxx", "xxxx", "000", "0000") and (
            len(cvv) not in (3, 4) or not cvv.isdigit()
        ):
            return False
        return True
    except (ValueError, AttributeError):
        return False
