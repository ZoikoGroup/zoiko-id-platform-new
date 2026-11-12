"""Spanish text for the credential reason codes. MACHINE DRAFT: written without a native-speaker or legal review (ZID-GAP-001 GID-062).

The codes and ids are locale-free; only this text is translated, and English stays the source. `reasons.describe` reports
`translation: MACHINE_DRAFT_UNREVIEWED` for it, so no screen presents it as reviewed. A reviewer replaces entries in place; nothing else changes.
"""

from __future__ import annotations

TEXT: dict[str, tuple[str, str, str]] = {
    "SOURCE_UNAVAILABLE": (
        "No se pudo contactar con la fuente",
        "La organización que guarda este registro no respondió. Eso no dice nada sobre usted.",
        "Inténtelo de nuevo más tarde. No tiene que hacer nada.",
    ),
    "NO_SOURCE_CAN_CONFIRM": (
        "Ninguna fuente puede confirmarlo",
        "La organización que guardaría este registro ya no existe o no puede responder.",
        "Puede enviar otras pruebas para que una persona las revise.",
    ),
    "SOURCE_HAS_NO_RECORD": (
        "La fuente no tiene ningún registro",
        "La fuente respondió y no tiene nada con estos datos.",
        "Compruebe los datos que indicó o envíe otras pruebas para que una persona las revise.",
    ),
    "PRIMARY_SOURCE_NOT_CONFIRMED": (
        "No confirmado por la fuente",
        "Se comprobó un documento o una firma, pero la organización que guarda el registro no lo ha confirmado. Un documento que parece correcto no es una confirmación.",
        "Hace falta una comprobación con la fuente.",
    ),
    "ISSUER_NOT_AUTHENTICATED": (
        "No se pudo confirmar al emisor",
        "No pudimos confirmar que esta organización sea la que lo emitió.",
        "Pida al emisor que lo vuelva a enviar o solicite que lo revise una persona.",
    ),
    "SUBJECT_NOT_CHECKED": (
        "No se comparó a nadie",
        "La comprobación se detuvo antes de comparar el registro con usted.",
        "No tiene que hacer nada, salvo que el motivo anterior indique otra cosa.",
    ),
    "SUBJECT_MATCH_AMBIGUOUS": (
        "Una persona debe decidir",
        "El registro podría ser suyo o de alguien con un nombre parecido. No se decide nada en su contra por un nombre parecido.",
        "Un revisor comparará los datos. Puede añadir pruebas.",
    ),
    "SUBJECT_NOT_MATCHED": (
        "El registro es de otra persona",
        "El registro encontrado no coincide con sus datos.",
        "Si cree que es un error, presente un recurso y añada pruebas, por ejemplo un cambio legal de nombre.",
    ),
    "AWARD_STATUS_NOT_CONFERRED": (
        "El título no fue concedido",
        "La institución registra la asistencia u otro estado, no un título concedido. Haber asistido no es lo mismo que graduarse.",
        "Si cree que el título fue concedido, presente un recurso con su número de certificado.",
    ),
    "ISSUER_LOOKALIKE": (
        "El emisor se parece a uno conocido",
        "El nombre se parece al de una organización conocida, pero no es el mismo. No adivinamos.",
        "Una persona comprobará de qué organización se trata.",
    ),
    "ISSUER_NOT_TRUSTED": (
        "El emisor no está en nuestra lista de confianza",
        "No tenemos constancia de que esta organización pueda emitir este tipo de credencial.",
        "Pida al emisor una credencial de una fuente reconocida.",
    ),
    "CPD_REQUIREMENT_NOT_CONFIGURED": (
        "El requisito no está configurado",
        "No podemos decir si este registro cumple la norma, porque la norma no se ha configurado para esta comprobación.",
        "Una persona lo revisará. No tiene que hacer nada.",
    ),
    "CLAIM_VERIFIED": (
        "La afirmación fue confirmada",
        "La fuente confirmó esta afirmación en el momento indicado.",
        "No tiene que hacer nada.",
    ),
    "NO_REASON_RECORDED": (
        "No hay otro motivo registrado",
        "Este resultado no tiene un motivo más concreto registrado.",
        "Un revisor puede añadir contexto si presenta un recurso.",
    ),
    "STATUS_CHANGED_ACTIVE": (
        "El estado pasó a activo",
        "La fuente indica ahora que la credencial está activa.",
        "No tiene que hacer nada.",
    ),
    "NO_ACCREDITATION_RECORD": (
        "Sin registro de acreditación",
        "No tenemos registro de acreditación de esta institución, así que no podemos decir si el programa estaba acreditado. Eso no significa que no lo estuviera.",
        "Una persona puede comprobar la acreditación con el organismo acreditador.",
    ),
    "NOT_ACCREDITED_AT_AWARD_DATE": (
        "No estaba acreditado cuando se concedió",
        "El programa no estaba cubierto por una acreditación en la fecha en que se concedió el título, aunque lo esté ahora.",
        "Puede presentar un recurso con pruebas de la acreditación en la fecha de concesión.",
    ),
    "LICENSE_NOT_YET_EFFECTIVE": (
        "La licencia aún no ha empezado",
        "El regulador indica una fecha de inicio que todavía no ha llegado.",
        "Vuelva a comprobarlo después de la fecha de inicio.",
    ),
    "LICENSE_PERIOD_ENDED": (
        "El periodo de la licencia ha terminado",
        "El regulador indica una fecha de fin que ya ha pasado.",
        "Renueve la licencia con el regulador y solicite una nueva comprobación.",
    ),
    "NOT_IN_GOOD_STANDING": (
        "No está en regla",
        "El regulador indica que la licencia no está en regla.",
        "Resuélvalo con el regulador y solicite una nueva comprobación.",
    ),
    "CONDITIONS_UNSPECIFIED": (
        "Las condiciones no están especificadas",
        "El regulador indica que hay condiciones pero no cuáles son, así que no podemos considerar la licencia sin reservas.",
        "Pida al regulador las condiciones por escrito.",
    ),
    "STANDING_NOT_REPORTED": (
        "No se informó de la situación",
        "El regulador no dijo si la licencia está en regla.",
        "Solicite una nueva comprobación más tarde.",
    ),
    "RENEWAL_OVERDUE": (
        "Ha pasado la fecha de renovación",
        "El certificado debía renovarse y no se ha renovado.",
        "Renuévelo y solicite una nueva comprobación.",
    ),
    "PROGRAM_RETIRED_NO_RENEWAL": (
        "El programa se ha retirado",
        "El certificado es auténtico, pero el programa ya no emite renovaciones.",
        "No tiene que hacer nada para un título anterior.",
    ),
    "EVIDENCE_NOT_SOURCE_CONFIRMED": (
        "No confirmado por el proveedor",
        "Se presentó un certificado pero el proveedor no lo ha confirmado.",
        "Pida al proveedor que lo confirme directamente.",
    ),
    "PROVIDER_NOT_APPROVED": (
        "Proveedor no aprobado",
        "El registro se conserva, pero este proveedor no está aprobado para el requisito.",
        "Elija un proveedor aprobado.",
    ),
    "CREDITS_UNREADABLE": (
        "No se pudieron leer los créditos",
        "El número de créditos no estaba en un formato que podamos usar.",
        "Envíe el registro de nuevo.",
    ),
    "CREDITS_BELOW_REQUIREMENT": (
        "Todavía no hay suficientes créditos",
        "El registro muestra menos créditos que el requisito.",
        "Complete los créditos restantes y solicite una nueva comprobación.",
    ),
    "NO_RECORD_AT_THIS_SOURCE": (
        "No se encontró nada en este regulador",
        "Este regulador no tiene ningún registro desfavorable. Eso no dice nada sobre otros reguladores.",
        "No tiene que hacer nada.",
    ),
    "FINDING_NOT_SOURCE_CONFIRMED": (
        "La resolución no está confirmada",
        "Una posible resolución no fue confirmada por el regulador, así que no se decide nada en su contra.",
        "Una persona lo revisará.",
    ),
    "APPEAL_PENDING": (
        "La resolución está recurrida",
        "El regulador indica que hay un recurso en curso, por lo que no se trata como definitiva.",
        "Una persona lo revisará. Puede añadir pruebas.",
    ),
    "FINDING_NOT_FINAL": (
        "La resolución no es definitiva",
        "La decisión del regulador aún no es definitiva.",
        "Una persona lo revisará.",
    ),
    "INSURED_ENTITY_MISMATCH": (
        "La cobertura es de otra persona",
        "La póliza nombra a otra persona o a una empresa, no a usted. La póliza de una empresa no es una póliza personal.",
        "Presente una póliza a su nombre o que le nombre a usted.",
    ),
    "TERRITORY_NOT_COVERED": (
        "El lugar no está cubierto",
        "La póliza no cubre el lugar donde se haría el trabajo.",
        "Amplíe la cobertura o elija un lugar cubierto.",
    ),
    "COVER_ENDS_BEFORE_PERIOD": (
        "La cobertura termina demasiado pronto",
        "La póliza termina antes del periodo de trabajo.",
        "Renueve la cobertura y solicite una nueva comprobación.",
    ),
    "WORK_NOT_COVERED": (
        "El trabajo no está cubierto",
        "La póliza no incluye este tipo de trabajo.",
        "Pida a su aseguradora que lo añada.",
    ),
    "WORK_EXCLUDED": (
        "El trabajo está excluido",
        "La póliza excluye expresamente este trabajo.",
        "Elija otro trabajo u otra póliza.",
    ),
    "POLICY_DOES_NOT_MATCH_ENGAGEMENT": (
        "Este no es el lugar para el que se hicieron las normas",
        "La elegibilidad se decide para un lugar y un servicio. Esta decisión era para otro.",
        "Solicite una decisión para este lugar y servicio exactos.",
    ),
    "IDENTITY_NOT_VERIFIED": (
        "La identidad no está verificada",
        "La verificación de identidad es un paso aparte y no está completa.",
        "Complete la verificación de identidad.",
    ),
    "RESTRICTION_ON_RECORD": (
        "Hay una restricción registrada",
        "Se aplica una restricción confirmada y definitiva.",
        "Puede presentar un recurso con pruebas.",
    ),
    "INSURANCE_NOT_SATISFIED": (
        "El seguro no cumple el requisito",
        "La cobertura que consta no cumple las necesidades de este encargo.",
        "Consulte los motivos del seguro para ver qué corregir.",
    ),
    "INSURANCE_NOT_CHECKED": (
        "No se ha comprobado el seguro",
        "No consta ninguna comprobación de seguro verificada.",
        "Añada una comprobación de seguro.",
    ),
    "DISCIPLINARY_REVIEW_PENDING": (
        "Se está revisando un registro del regulador",
        "Un registro del regulador está recurrido o sin resolver, por lo que el encargo queda en manos de una persona.",
        "No tiene que hacer nada. Una persona decidirá.",
    ),
    "PROOF_INVALID": (
        "La firma no es válida",
        "No se pudo verificar la firma de la credencial.",
        "Pida al emisor que la vuelva a enviar.",
    ),
    "SEAL_INVALID": (
        "El sello no es válido",
        "No se pudo verificar el sello electrónico de esta credencial.",
        "Pida al emisor una copia válida.",
    ),
    "ISSUER_NOT_IN_REGISTRY": (
        "Firma auténtica de un emisor no reconocido",
        "La credencial no ha sido alterada, pero no reconocemos a esta organización como fuente para este tipo de credencial.",
        "Hace falta una fuente reconocida.",
    ),
    "HOLDER_BINDING_FAILED": (
        "No se demostró que sea suya",
        "No se demostró que la credencial pertenezca a quien la presenta. No se acepta una credencial copiada.",
        "Preséntela de nuevo desde la cartera a la que se emitió.",
    ),
    "STATUS_UNKNOWN": (
        "No se pudo leer el estado",
        "No pudimos comprobar si la credencial sigue siendo válida.",
        "Inténtelo de nuevo más tarde.",
    ),
    "NOT_YET_VALID": (
        "Todavía no es válida",
        "La fecha de inicio de la credencial aún no ha llegado.",
        "Inténtelo de nuevo después de la fecha de inicio.",
    ),
    "PAST_VALID_UNTIL": (
        "La credencial ha caducado",
        "La fecha de fin de la credencial ya ha pasado.",
        "Pida una credencial renovada.",
    ),
    "MATCHED_VIA_FORMER_NAME_WITH_EVIDENCE": (
        "Coincide mediante un nombre anterior",
        "El registro está a un nombre anterior y consta un registro del cambio de nombre.",
        "No tiene que hacer nada.",
    ),
    "FORMER_NAME_WITHOUT_CHANGE_EVIDENCE": (
        "Un nombre anterior necesita pruebas",
        "El registro está a un nombre anterior pero no consta ningún registro del cambio de nombre.",
        "Añada pruebas del cambio, como un registro de matrimonio o una resolución judicial.",
    ),
    "NAME_PARTIAL": (
        "El nombre solo coincide en parte",
        "Los nombres son parecidos pero no lo bastante para estar seguros.",
        "Una persona comparará los datos.",
    ),
    "SCRIPT_NOT_COMPARABLE": (
        "Una persona debe comparar los nombres",
        "Uno de los nombres está escrito en un alfabeto que no se puede comparar automáticamente. Eso nunca se trata como si fuera otra persona.",
        "Un revisor comparará los nombres. Puede añadir un documento que muestre ambas grafías.",
    ),
    "NAME_DIFFERENT": (
        "Los nombres son distintos",
        "El registro tiene un nombre diferente.",
        "Si es un error, presente un recurso y añada pruebas.",
    ),
    "BIRTH_YEAR_CONFLICT": (
        "El año de nacimiento es distinto",
        "El nombre coincide pero el año de nacimiento no, así que no se decide nada.",
        "Una persona lo revisará. Puede añadir pruebas.",
    ),
    "ISSUER_DENIED": (
        "El emisor dice que no lo emitió",
        "La organización respondió que no emitió esta credencial.",
        "Puede presentar un recurso con pruebas.",
    ),
    "ISSUER_NOT_HELD": (
        "El emisor no tiene ese registro",
        "La organización respondió que no tiene ese registro.",
        "Compruebe los datos o envíe otras pruebas.",
    ),
    "ISSUER_DID_NOT_ANSWER": (
        "El emisor no respondió a tiempo",
        "Preguntamos a la organización y no recibimos respuesta antes del plazo. Eso no dice nada sobre usted.",
        "Puede enviar otras pruebas para que una persona las revise.",
    ),
    "RESPONSE_REJECTED_BY_REVIEW": (
        "No se aceptó la respuesta",
        "La respuesta recibida no superó la revisión, así que no se utilizó.",
        "No tiene que hacer nada. Es posible que volvamos a preguntar al emisor.",
    ),
    "SIGNED_DOCUMENT_CHAINS_TO_A_CONFIGURED_ANCHOR": (
        "El documento está firmado con un certificado de confianza",
        "La firma es válida y nada cambió después. Eso muestra que el documento está tal como se firmó. No muestra que quien firmó tuviera derecho a firmar para este fin.",
        "Una persona decide si el firmante tenía la autoridad necesaria.",
    ),
    "NO_LEGAL_ENTITY_IDENTIFIER_GIVEN": (
        "No se indicó el identificador de la empresa",
        "No se envió ningún identificador de entidad jurídica, así que no se comprobó nada sobre la empresa.",
        "Añada el identificador de la empresa si quiere que se compruebe.",
    ),
    "LEI_MALFORMED": (
        "El identificador de la empresa no tiene el formato correcto",
        "Un identificador de entidad jurídica tiene 20 letras y dígitos. Este no los tiene.",
        "Revise el identificador y envíelo de nuevo.",
    ),
    "LEI_CHECK_DIGITS_INVALID": (
        "El identificador de la empresa tiene un error de escritura",
        "Los dos últimos dígitos de un identificador de entidad jurídica prueban que se copió bien. Estos no coinciden.",
        "Compare el identificador con los registros de la empresa y envíelo de nuevo.",
    ),
    "LEI_WELL_FORMED_EXISTENCE_NOT_LOOKED_UP_HERE": (
        "El identificador de la empresa tiene el formato correcto",
        "Parece un identificador válido, pero esta comprobación no buscó la empresa, así que no demuestra que exista ni que esté activa.",
        "Use la comprobación de organizaciones para buscar la empresa en un registro oficial.",
    ),
    "RIGHT_TO_RENT_REQUIRES_A_REGISTERED_PROVIDER": (
        "Se necesita un proveedor registrado para esta comprobación",
        "En Inglaterra la comprobación digital del derecho a alquilar debe hacerla un proveedor registrado para ello. Este servicio no es ese proveedor.",
        "Use un proveedor registrado para la comprobación. Sus obligaciones como arrendador no cambian.",
    ),
    "NO_RULE_FOR_THIS_JURISDICTION": (
        "Aquí no hay una norma para este lugar",
        "Una norma de otro lugar nunca se aplica por defecto, así que no se decidió nada para esta ubicación.",
        "Una persona revisará qué se aplica donde está la propiedad.",
    ),
    "IDENTITY_IS_CHECKED_BY_THE_IDENTITY_AND_CREDENTIAL_ROUTES": (
        "La identidad se comprueba en otro sitio",
        "Esta comprobación no mira quién es la persona. La identidad tiene su propia comprobación.",
        "Ejecute la comprobación de identidad y use su resultado junto con esta.",
    ),
}
