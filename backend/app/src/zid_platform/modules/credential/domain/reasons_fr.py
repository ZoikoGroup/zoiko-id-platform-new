"""French text for the credential reason codes. MACHINE DRAFT: written without a native-speaker or legal review (ZID-GAP-001 GID-062).

The codes and ids are locale-free; only this text is translated, and English stays the source. `reasons.describe` reports
`translation: MACHINE_DRAFT_UNREVIEWED` for it, so no screen presents it as reviewed. A reviewer replaces entries in place; nothing else changes.
"""

from __future__ import annotations

TEXT: dict[str, tuple[str, str, str]] = {
    "SOURCE_UNAVAILABLE": (
        "La source n'a pas pu être jointe",
        "L'organisme qui détient ce dossier n'a pas répondu. Cela ne dit rien sur vous.",
        "Réessayez plus tard. Vous n'avez rien à faire.",
    ),
    "NO_SOURCE_CAN_CONFIRM": (
        "Aucune source ne peut le confirmer",
        "L'organisme qui détiendrait ce dossier n'existe plus ou ne peut pas répondre.",
        "Vous pouvez envoyer d'autres justificatifs pour qu'une personne les examine.",
    ),
    "SOURCE_HAS_NO_RECORD": (
        "La source n'a aucun dossier",
        "La source a répondu et n'a rien sous ces informations.",
        "Vérifiez les informations fournies ou envoyez d'autres justificatifs pour qu'une personne les examine.",
    ),
    "PRIMARY_SOURCE_NOT_CONFIRMED": (
        "Non confirmé par la source",
        "Un document ou une signature a été vérifié, mais l'organisme qui détient le dossier ne l'a pas confirmé. Un document d'apparence correcte n'est pas une confirmation.",
        "Une vérification auprès de la source est nécessaire.",
    ),
    "ISSUER_NOT_AUTHENTICATED": (
        "L'émetteur n'a pas pu être confirmé",
        "Nous n'avons pas pu confirmer que cet organisme est bien celui qui l'a émis.",
        "Demandez à l'émetteur de le fournir à nouveau, ou demandez un examen par une personne.",
    ),
    "SUBJECT_NOT_CHECKED": (
        "Personne n'a été comparé",
        "La vérification s'est arrêtée avant que le dossier soit comparé avec vous.",
        "Vous n'avez rien à faire, sauf si le motif ci-dessus indique autre chose.",
    ),
    "SUBJECT_MATCH_AMBIGUOUS": (
        "Une personne doit trancher",
        "Le dossier pourrait être le vôtre ou celui d'une personne au nom proche. Rien de défavorable n'est décidé sur un nom proche.",
        "Un examinateur comparera les informations. Vous pouvez ajouter des justificatifs.",
    ),
    "SUBJECT_NOT_MATCHED": (
        "Le dossier concerne quelqu'un d'autre",
        "Le dossier trouvé ne correspond pas à vos informations.",
        "Si vous pensez que c'est une erreur, faites appel et ajoutez des justificatifs, par exemple un changement de nom légal.",
    ),
    "AWARD_STATUS_NOT_CONFERRED": (
        "Le diplôme n'a pas été conféré",
        "L'établissement enregistre une inscription ou un autre statut, pas un diplôme conféré. Avoir suivi un cursus n'est pas être diplômé.",
        "Si vous pensez que le diplôme a été conféré, faites appel avec votre numéro de certificat.",
    ),
    "ISSUER_LOOKALIKE": (
        "L'émetteur ressemble à un émetteur connu",
        "Le nom est proche de celui d'un organisme connu sans être identique. Nous ne devinons pas.",
        "Une personne vérifiera de quel organisme il s'agit.",
    ),
    "ISSUER_NOT_TRUSTED": (
        "L'émetteur ne figure pas sur notre liste de confiance",
        "Rien n'indique que cet organisme puisse émettre ce type d'attestation.",
        "Demandez à l'émetteur une attestation provenant d'une source reconnue.",
    ),
    "CPD_REQUIREMENT_NOT_CONFIGURED": (
        "L'exigence n'est pas configurée",
        "Nous ne pouvons pas dire si ce dossier respecte la règle, car la règle n'a pas été configurée pour cette vérification.",
        "Une personne l'examinera. Vous n'avez rien à faire.",
    ),
    "CLAIM_VERIFIED": (
        "L'affirmation a été confirmée",
        "La source a confirmé cette affirmation au moment indiqué.",
        "Vous n'avez rien à faire.",
    ),
    "NO_REASON_RECORDED": (
        "Aucun autre motif enregistré",
        "Ce résultat n'a pas de motif plus précis enregistré.",
        "Un examinateur peut ajouter du contexte si vous faites appel.",
    ),
    "STATUS_CHANGED_ACTIVE": (
        "Le statut est passé à actif",
        "La source indique désormais que l'attestation est active.",
        "Vous n'avez rien à faire.",
    ),
    "NO_ACCREDITATION_RECORD": (
        "Aucun dossier d'accréditation",
        "Nous n'avons aucun dossier d'accréditation pour cet établissement ; nous ne pouvons donc pas dire si le programme était accrédité. Cela ne signifie pas qu'il ne l'était pas.",
        "Une personne peut vérifier l'accréditation auprès de l'organisme accréditeur.",
    ),
    "NOT_ACCREDITED_AT_AWARD_DATE": (
        "Non accrédité à la date d'obtention",
        "Le programme n'était pas couvert par une accréditation à la date de remise du diplôme, même s'il l'est aujourd'hui.",
        "Vous pouvez faire appel avec une preuve d'accréditation à la date d'obtention.",
    ),
    "UNKNOWN_HISTORICAL_ISSUER": (
        "Nous ne connaissons pas cet établissement",
        "Nous n'avons aucun historique enregistré de cet établissement au moment de la vérification ; nous ne pouvons donc pas dire qu'il était autorisé à délivrer cette attestation.",
        "Une personne peut vérifier l'établissement auprès d'une source officielle.",
    ),
    "AUTHORITY_NOT_RECORDED": (
        "L'autorité de l'établissement n'est pas enregistrée",
        "Nous n'avons enregistré aucune base légale ou réglementaire permettant à cet établissement de délivrer ce type d'attestation.",
        "Une personne peut vérifier l'autorité de l'établissement auprès d'une source officielle.",
    ),
    "ISSUER_REVOKED_AT_AWARD_DATE": (
        "L'établissement n'était plus reconnu",
        "La reconnaissance de l'établissement avait été retirée à la date de remise du diplôme.",
        "Vous pouvez faire appel avec une preuve qu'il était reconnu à la date d'obtention.",
    ),
    "NOT_TRUSTED_FOR_CLAIM_TYPE": (
        "L'établissement n'est pas enregistré pour ce type de déclaration",
        "Cet établissement n'est pas enregistré comme autorité pour ce type d'attestation.",
        "Une personne peut vérifier si une autre source peut le confirmer.",
    ),
    "QUALIFICATION_VERIFIED": (
        "La qualification est vérifiée",
        "La qualification elle-même a été contrôlée et confirmée. C'est distinct du droit de cette personne d'exercer ce service ici.",
        "Vous n'avez rien à faire pour la qualification.",
    ),
    "PRACTICE_ELIGIBILITY_NOT_ESTABLISHED": (
        "Le droit d'exercer n'est pas établi",
        "Nous n'avons pas pu établir que cette personne peut proposer ce service à cet endroit, même si d'autres contrôles ont réussi.",
        "Une personne peut l'examiner, ou vous pouvez fournir les preuves manquantes.",
    ),
    "CONDITION_BLOCKS_SERVICE": (
        "Une condition de la licence ferme ce service",
        "Une condition de la licence signifie que ce service n'est pas ouvert à cette personne à cet endroit.",
        "Vous pouvez faire appel avec une preuve que la condition ne s'applique plus.",
    ),
    "CONDITION_NEEDS_REVIEW": (
        "Une condition de la licence doit être examinée",
        "Une condition de la licence doit être examinée avant que ce service puisse se poursuivre.",
        "Un examinateur l'examinera ; vous pouvez ajouter du contexte.",
    ),
    "CONDITION_NOT_CLASSIFIED": (
        "Une condition de la licence n'est pas reconnue",
        "Une condition de la licence ne correspond à aucune de nos règles ; nous ne pouvons donc pas la traiter comme autorisant ce service.",
        "Un examinateur l'examinera.",
    ),
    "COVERAGE_LIMIT_NOT_REPORTED": (
        "Le plafond de garantie n'est pas indiqué",
        "Le dossier d'assurance n'indique aucun plafond de garantie ; nous ne pouvons donc pas savoir s'il suffit.",
        "Demandez à l'assureur de confirmer le plafond.",
    ),
    "COVERAGE_CURRENCY_MISMATCH": (
        "La garantie est dans une autre devise",
        "Le plafond de garantie est dans une devise différente de celle exigée. Nous ne convertissons pas les devises.",
        "Fournissez une garantie dans la devise exigée.",
    ),
    "COVERAGE_LIMIT_BELOW_MINIMUM": (
        "Le plafond de garantie est trop bas",
        "Le plafond de garantie est inférieur au minimum exigé pour ce service.",
        "Augmentez la garantie ou fournissez une garantie complémentaire.",
    ),
    "PRACTICE_CLASS_NOT_COVERED": (
        "La garantie concerne un autre type d'exercice",
        "Le dossier d'assurance ne couvre pas le type d'exercice dont ce service a besoin.",
        "Demandez à l'assureur de confirmer la garantie pour cet exercice.",
    ),
    "COVER_BASIS_NOT_REPORTED": (
        "La base de la garantie n'est pas indiquée",
        "Le dossier d'assurance ne dit pas si la garantie est déclenchée par la réclamation ou par le fait dommageable.",
        "Demandez à l'assureur de confirmer la base de la garantie.",
    ),
    "RETROACTIVE_DATE_MISSING": (
        "La date de rétroactivité est absente",
        "Une garantie déclenchée par la réclamation exige une date de rétroactivité, et le dossier n'en donne aucune.",
        "Demandez à l'assureur de confirmer la date de rétroactivité.",
    ),
    "RETROACTIVE_DATE_AFTER_WORK_START": (
        "La garantie commence trop tard",
        "La date de rétroactivité de cette garantie est postérieure au début du travail ; le travail antérieur ne serait donc pas couvert.",
        "Fournissez une garantie avec une date de rétroactivité plus ancienne.",
    ),
    "ACCESS_CERTIFICATE_MISSING": (
        "La demande n'est pas signée avec un certificat enregistré",
        "Une demande à un portefeuille d'identité numérique de l'UE doit être signée avec le certificat enregistré à cet effet, et celle-ci ne l'était pas.",
        "Zoiko iD doit configurer son certificat enregistré avant d'interroger le portefeuille.",
    ),
    "ACCESS_CERTIFICATE_MISMATCH": (
        "La demande est signée avec un autre certificat",
        "La demande est signée avec un certificat différent de celui enregistré pour les portefeuilles d'identité numérique de l'UE.",
        "Zoiko iD doit signer avec son certificat enregistré.",
    ),
    "INTENDED_USE_NOT_REGISTERED": (
        "Cet usage n'est pas enregistré",
        "Zoiko iD n'est pas enregistré pour demander des informations à un portefeuille d'identité numérique de l'UE à cette fin.",
        "Vous n'avez rien à faire ; la demande n'a pas été envoyée.",
    ),
    "ATTESTATION_TYPE_NOT_REGISTERED": (
        "Ce type d'attestation n'est pas enregistré",
        "Zoiko iD n'est pas enregistré pour demander ce type d'attestation à un portefeuille d'identité numérique de l'UE à cette fin.",
        "Vous n'avez rien à faire ; la demande n'a pas été envoyée.",
    ),
    "PID_PROVIDER_NOT_LISTED": (
        "Le fournisseur des données d'identité n'est pas reconnu",
        "Les données d'identification personnelle ne proviennent pas d'un fournisseur que nous pouvons confirmer sur une liste officielle.",
        "Utilisez un autre moyen de vérifier votre identité.",
    ),
    "QEAA_STATUS_NOT_ESTABLISHED": (
        "Ce n'est pas une attestation qualifiée",
        "L'émetteur n'a pas été confirmé comme prestataire qualifié pour cette attestation à sa date d'émission ; elle est donc traitée comme une attestation ordinaire.",
        "Vous n'avez rien à faire ; l'attestation est toujours lue, à son juste niveau.",
    ),
    "LICENSE_NOT_YET_EFFECTIVE": (
        "La licence n'a pas encore commencé",
        "Le régulateur indique une date de début qui n'est pas encore arrivée.",
        "Vérifiez de nouveau après la date de début.",
    ),
    "LICENSE_PERIOD_ENDED": (
        "La période de la licence est terminée",
        "Le régulateur indique une date de fin qui est dépassée.",
        "Renouvelez auprès du régulateur, puis demandez une nouvelle vérification.",
    ),
    "NOT_IN_GOOD_STANDING": (
        "Pas en règle",
        "Le régulateur indique que la licence n'est pas en règle.",
        "Régularisez auprès du régulateur, puis demandez une nouvelle vérification.",
    ),
    "CONDITIONS_UNSPECIFIED": (
        "Les conditions ne sont pas précisées",
        "Le régulateur signale des conditions sans dire lesquelles ; nous ne pouvons donc pas considérer la licence comme sans réserve.",
        "Demandez les conditions par écrit au régulateur.",
    ),
    "STANDING_NOT_REPORTED": (
        "La situation n'a pas été communiquée",
        "Le régulateur n'a pas dit si la licence est en règle.",
        "Demandez une nouvelle vérification plus tard.",
    ),
    "RENEWAL_OVERDUE": (
        "La date de renouvellement est dépassée",
        "Le certificat devait être renouvelé et ne l'a pas été.",
        "Renouvelez-le, puis demandez une nouvelle vérification.",
    ),
    "PROGRAM_RETIRED_NO_RENEWAL": (
        "Le programme a été retiré",
        "Le certificat est authentique, mais le programme n'émet plus de renouvellements.",
        "Vous n'avez rien à faire pour un diplôme passé.",
    ),
    "EVIDENCE_NOT_SOURCE_CONFIRMED": (
        "Non confirmé par le prestataire",
        "Un certificat a été fourni mais le prestataire ne l'a pas confirmé.",
        "Demandez au prestataire de le confirmer directement.",
    ),
    "PROVIDER_NOT_APPROVED": (
        "Prestataire non agréé",
        "Le dossier est conservé, mais ce prestataire n'est pas agréé pour cette exigence.",
        "Choisissez un prestataire agréé.",
    ),
    "CREDITS_UNREADABLE": (
        "Les crédits n'ont pas pu être lus",
        "Le nombre de crédits n'était pas dans une forme exploitable.",
        "Envoyez de nouveau le dossier.",
    ),
    "CREDITS_BELOW_REQUIREMENT": (
        "Pas encore assez de crédits",
        "Le dossier montre moins de crédits que l'exigence.",
        "Complétez les crédits manquants, puis demandez une nouvelle vérification.",
    ),
    "NO_RECORD_AT_THIS_SOURCE": (
        "Rien trouvé auprès de ce régulateur",
        "Ce régulateur ne détient aucun dossier défavorable. Cela ne dit rien sur les autres régulateurs.",
        "Vous n'avez rien à faire.",
    ),
    "FINDING_NOT_SOURCE_CONFIRMED": (
        "La décision n'est pas confirmée",
        "Une décision possible n'a pas été confirmée par le régulateur ; rien de défavorable n'est donc décidé.",
        "Une personne l'examinera.",
    ),
    "APPEAL_PENDING": (
        "La décision fait l'objet d'un appel",
        "Le régulateur indique qu'un appel est en cours ; elle n'est donc pas considérée comme définitive.",
        "Une personne l'examinera. Vous pouvez ajouter des justificatifs.",
    ),
    "FINDING_NOT_FINAL": (
        "La décision n'est pas définitive",
        "La décision du régulateur n'est pas devenue définitive.",
        "Une personne l'examinera.",
    ),
    "INSURED_ENTITY_MISMATCH": (
        "La couverture concerne quelqu'un d'autre",
        "La police désigne une autre personne ou une société, pas vous. La police d'une société n'est pas une police personnelle.",
        "Fournissez une police à votre nom ou qui vous désigne.",
    ),
    "TERRITORY_NOT_COVERED": (
        "Le lieu n'est pas couvert",
        "La police ne couvre pas le lieu où le travail serait effectué.",
        "Étendez la couverture ou choisissez un lieu couvert.",
    ),
    "COVER_ENDS_BEFORE_PERIOD": (
        "La couverture se termine trop tôt",
        "La police se termine avant la période de travail.",
        "Renouvelez la couverture, puis demandez une nouvelle vérification.",
    ),
    "WORK_NOT_COVERED": (
        "Le travail n'est pas couvert",
        "La police ne mentionne pas ce type de travail.",
        "Demandez à votre assureur de l'ajouter.",
    ),
    "WORK_EXCLUDED": (
        "Le travail est exclu",
        "La police exclut expressément ce travail.",
        "Choisissez un autre travail ou une autre police.",
    ),
    "POLICY_DOES_NOT_MATCH_ENGAGEMENT": (
        "Ce n'est pas le lieu pour lequel les règles ont été établies",
        "L'éligibilité est décidée pour un lieu et un service. Cette décision concernait un autre cas.",
        "Demandez une décision pour ce lieu et ce service précis.",
    ),
    "IDENTITY_NOT_VERIFIED": (
        "L'identité n'est pas vérifiée",
        "La vérification d'identité est une étape distincte et elle n'est pas terminée.",
        "Terminez la vérification d'identité.",
    ),
    "CREDENTIAL_SIGNATURE_NOT_VALID": (
        "La signature de l'attestation n'est pas valide",
        "L'attestation a été modifiée après sa signature, ou elle n'a pas été signée par la clé publiée par son émetteur.",
        "Demandez une nouvelle attestation à l'émetteur. Ne vous fiez pas à celle-ci.",
    ),
    "CREDENTIAL_TYPE_NOT_ACCEPTED_AS_EVIDENCE_OF_IDENTITY": (
        "Ce type d'attestation n'est pas accepté comme preuve d'identité",
        "L'attestation peut être authentique, mais son type ne figure pas dans la liste des attestations acceptées comme preuve d'identité.",
        "Présentez une attestation d'identité acceptée ou utilisez un autre moyen de vérifier l'identité.",
    ),
    "CREDENTIAL_STATUS_NOT_CONFIRMED": (
        "Le statut de l'attestation n'a pas pu être confirmé",
        "Les informations de statut de l'émetteur étaient absentes ou illisibles, donc on ne sait pas si l'attestation est toujours valide.",
        "Réessayez plus tard ou présentez une attestation dont l'émetteur publie le statut.",
    ),
    "BIRTH_DATE_NOT_A_VALID_DATE": (
        "La date de naissance de l'attestation n'est pas une date valide",
        "La date de naissance n'a pas la forme attendue, se situe dans le futur ou est invraisemblablement ancienne.",
        "Demandez à l'émetteur de corriger l'attestation.",
    ),
    "RESTRICTION_ON_RECORD": (
        "Une restriction est enregistrée",
        "Une restriction confirmée et définitive s'applique.",
        "Vous pouvez faire appel avec des justificatifs.",
    ),
    "INSURANCE_NOT_SATISFIED": (
        "L'assurance ne respecte pas l'exigence",
        "La couverture au dossier ne répond pas aux besoins de cette mission.",
        "Consultez les motifs d'assurance pour savoir quoi corriger.",
    ),
    "INSURANCE_NOT_CHECKED": (
        "L'assurance n'a pas été vérifiée",
        "Aucune vérification d'assurance validée n'est au dossier.",
        "Ajoutez une vérification d'assurance.",
    ),
    "DISCIPLINARY_REVIEW_PENDING": (
        "Un dossier du régulateur est en cours d'examen",
        "Un dossier du régulateur est en appel ou non résolu ; la mission est donc confiée à une personne.",
        "Vous n'avez rien à faire. Une personne décidera.",
    ),
    "PROOF_INVALID": (
        "La signature n'est pas valide",
        "La signature de l'attestation n'a pas pu être vérifiée.",
        "Demandez à l'émetteur de la fournir à nouveau.",
    ),
    "SEAL_INVALID": (
        "Le cachet n'est pas valide",
        "Le cachet électronique de cette attestation n'a pas pu être vérifié.",
        "Demandez à l'émetteur une copie valide.",
    ),
    "ISSUER_NOT_IN_REGISTRY": (
        "Signature authentique d'un émetteur non reconnu",
        "L'attestation n'a pas été modifiée, mais nous ne reconnaissons pas cet organisme comme source pour ce type d'attestation.",
        "Une source reconnue est nécessaire.",
    ),
    "HOLDER_BINDING_FAILED": (
        "Il n'est pas démontré qu'elle est à vous",
        "Il n'a pas été démontré que l'attestation appartient à la personne qui la présente. Une attestation copiée n'est pas acceptée.",
        "Présentez-la de nouveau depuis le portefeuille auquel elle a été émise.",
    ),
    "STATUS_UNKNOWN": (
        "Le statut n'a pas pu être lu",
        "Nous n'avons pas pu vérifier si l'attestation est toujours valide.",
        "Réessayez plus tard.",
    ),
    "NOT_YET_VALID": (
        "Pas encore valide",
        "La date de début de l'attestation n'est pas encore arrivée.",
        "Réessayez après la date de début.",
    ),
    "PAST_VALID_UNTIL": (
        "L'attestation a expiré",
        "La date de fin de l'attestation est dépassée.",
        "Demandez une attestation renouvelée.",
    ),
    "MATCHED_VIA_FORMER_NAME_WITH_EVIDENCE": (
        "Correspondance par un ancien nom",
        "Le dossier est à un ancien nom et un justificatif de changement de nom est au dossier.",
        "Vous n'avez rien à faire.",
    ),
    "FORMER_NAME_WITHOUT_CHANGE_EVIDENCE": (
        "Un ancien nom nécessite un justificatif",
        "Le dossier est à un ancien nom mais aucun justificatif de changement de nom n'est au dossier.",
        "Ajoutez un justificatif du changement, tel qu'un acte de mariage ou une décision de justice.",
    ),
    "NAME_PARTIAL": (
        "Le nom ne correspond qu'en partie",
        "Les noms sont proches mais pas assez pour en être sûr.",
        "Une personne comparera les informations.",
    ),
    "SCRIPT_NOT_COMPARABLE": (
        "Une personne doit comparer les noms",
        "L'un des noms est écrit dans une écriture qui ne peut pas être comparée automatiquement. Cela n'est jamais traité comme une personne différente.",
        "Un examinateur comparera les noms. Vous pouvez ajouter un document montrant les deux orthographes.",
    ),
    "NAME_DIFFERENT": (
        "Les noms sont différents",
        "Le dossier porte un nom différent.",
        "Si c'est une erreur, faites appel et ajoutez des justificatifs.",
    ),
    "BIRTH_YEAR_CONFLICT": (
        "L'année de naissance diffère",
        "Le nom correspond mais pas l'année de naissance ; rien n'est donc décidé.",
        "Une personne l'examinera. Vous pouvez ajouter des justificatifs.",
    ),
    "ISSUER_DENIED": (
        "L'émetteur dit ne pas l'avoir émis",
        "L'organisme a répondu qu'il n'a pas émis cette attestation.",
        "Vous pouvez faire appel avec des justificatifs.",
    ),
    "ISSUER_NOT_HELD": (
        "L'émetteur n'a pas ce dossier",
        "L'organisme a répondu qu'il n'a pas un tel dossier.",
        "Vérifiez les informations ou envoyez d'autres justificatifs.",
    ),
    "ISSUER_DID_NOT_ANSWER": (
        "L'émetteur n'a pas répondu à temps",
        "Nous avons interrogé l'organisme sans réponse avant l'échéance. Cela ne dit rien sur vous.",
        "Vous pouvez envoyer d'autres justificatifs pour qu'une personne les examine.",
    ),
    "RESPONSE_REJECTED_BY_REVIEW": (
        "La réponse n'a pas été acceptée",
        "La réponse reçue n'a pas passé l'examen ; elle n'a donc pas été utilisée.",
        "Vous n'avez rien à faire. Nous pourrons interroger de nouveau l'émetteur.",
    ),
    "SIGNED_DOCUMENT_CHAINS_TO_A_CONFIGURED_ANCHOR": (
        "Le document est signé avec un certificat de confiance",
        "La signature est valide et rien n'a changé après. Cela montre que le document est tel qu'il a été signé. Cela ne montre pas que le signataire avait le droit de signer à cette fin.",
        "Une personne décide si le signataire avait l'autorité nécessaire.",
    ),
    "NO_LEGAL_ENTITY_IDENTIFIER_GIVEN": (
        "Aucun identifiant d'entreprise n'a été fourni",
        "Aucun identifiant d'entité juridique n'a été transmis, donc rien n'a été vérifié sur l'entreprise.",
        "Ajoutez l'identifiant de l'entreprise si vous voulez qu'il soit vérifié.",
    ),
    "LEI_MALFORMED": (
        "L'identifiant de l'entreprise n'a pas la bonne forme",
        "Un identifiant d'entité juridique comporte 20 lettres et chiffres. Celui-ci n'en comporte pas 20.",
        "Vérifiez l'identifiant et renvoyez-le.",
    ),
    "LEI_CHECK_DIGITS_INVALID": (
        "L'identifiant de l'entreprise contient une faute de frappe",
        "Les deux derniers chiffres d'un identifiant d'entité juridique prouvent qu'il a été bien recopié. Ceux-ci ne correspondent pas.",
        "Comparez l'identifiant avec les registres de l'entreprise et renvoyez-le.",
    ),
    "LEI_WELL_FORMED_EXISTENCE_NOT_LOOKED_UP_HERE": (
        "L'identifiant de l'entreprise a la bonne forme",
        "Il ressemble à un identifiant valide, mais cette vérification n'a pas recherché l'entreprise : elle ne prouve donc pas que l'entreprise existe ni qu'elle est active.",
        "Utilisez la vérification d'organisation pour rechercher l'entreprise dans un registre officiel.",
    ),
    "RIGHT_TO_RENT_REQUIRES_A_REGISTERED_PROVIDER": (
        "Un prestataire enregistré est nécessaire pour cette vérification",
        "En Angleterre, la vérification numérique du droit de louer doit être faite par un prestataire enregistré pour cela. Ce service n'est pas ce prestataire.",
        "Utilisez un prestataire enregistré pour la vérification. Vos obligations de bailleur ne changent pas.",
    ),
    "NO_RULE_FOR_THIS_JURISDICTION": (
        "Il n'y a pas de règle ici pour ce lieu",
        "Une règle d'un autre lieu n'est jamais appliquée par défaut, donc rien n'a été décidé pour cet endroit.",
        "Une personne examinera ce qui s'applique là où se trouve le bien.",
    ),
    "IDENTITY_IS_CHECKED_BY_THE_IDENTITY_AND_CREDENTIAL_ROUTES": (
        "L'identité est vérifiée ailleurs",
        "Cette vérification ne regarde pas qui est la personne. L'identité a sa propre vérification.",
        "Lancez la vérification d'identité, puis utilisez son résultat avec celle-ci.",
    ),
}
