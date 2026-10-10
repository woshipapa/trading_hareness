"""Pure readers for TDX local metadata and non-A-share files.

The layouts here are based on the public pytdx/mootdx/tdxrs/injoyai sources.
They intentionally return research-shaped dictionaries and never write to the
platform database.  Real-client verification is still required for private
TDX versions, especially TNF and GPJY files.
"""

from __future__ import annotations

import struct
from typing import Any, Mapping, Sequence


MASK32 = 0xFFFFFFFF
GBBQ_RECORD_SIZE = 29
GBBQ_CATEGORY_NAMES = {
    1: "除权除息", 2: "送配股上市", 3: "非流通股上市", 4: "未知股本变动",
    5: "股本变化", 6: "增发新股", 7: "股份回购", 8: "增发新股上市",
    9: "转配股上市", 10: "可转债上市", 11: "扩缩股", 12: "非流通股缩股",
    13: "送认购权证", 14: "送认沽权证",
}

# Published by rainx/pytdx's gbbq_reader.py.  Filled below as a compact hex
# table to keep the implementation stdlib-only and independent of pytdx.
_GBBQ_KEY_HEX = (
    "38A7C21DE06A17E2D139A2409CBA46AF42C6FF0574EADABB89B4F844AC89D7F2987FB6BCE4F76B750504586779C86DC6\n2B06968CFB86068BBFD6E8E187496B36C71802795325727213CC040B90240CDCDB031AD52E04855C7E8EBD02262DBD06\n1B5034991BA22404F28835C889EAD5FB1224BBB53B29CA14A604CEA9A85802B9AAE397A3A62257BBADA0225FEB058611\nC3EDB13F39C236D14A43C8644DB06E3A7C516DF78EC6DFF38EA41E749DB222054D073F967F97F963B9C42B9875F6D684\n56DC15D3528B60F3D60EA9AD0707E9028658C2329C90BCC919BFB0547AF8CCA827638229EEFB9811BF352962919395FC\nF4F008E4B23AB45EB3B02E3E20C1D743597DC6295F69747FB277E10EFA85A1C9777383B3CB1C60DBE95369FCB3185915\n0F978A7AC883F549DC1B3E86C1954546E216677F1235A0BB27FBCCF8307E4FC86DAB18B20D01CC7920807BFA37AA149E\n85E825E9D42D354E8FD3DEB0068D15155265E8390328090267993D13BAF3685C4C89B0E36BAE165C8825F8330319025B\n297B2A412D7549489BB3B6B3BFAADF8C95FE0F13B87B02BB52E11C34C39B8759E246CC22774BD7C42C31AA847C445188\n151ACCAE409D1F4497299845607447A10DA573F053FF01F9F49AF13607D02DA0792D812325AD4B9CC8BC12554DD4BB95\nB1B9BE7DA6E6A053BA838CDD7EE94BEDBA2842D8FF986935CA4E9C9D57D6CFA0895CA2E754D2AF4CFB54C4B44FC3BAF8\nA2586919790EA80E3DC804FD2632C8E1028BA71CC39125E5D849DBDF195F16F5A78B182304D4BFFB44C4617C796EC890\n15B5EB5087CA7A69472FAFA8B5A28A84C44179E8DE0CACD0D56F34C6CBA776F900244205267E7B1486597BDB1C62D5B7\n3EF71744274BD2C66FFFC84955AD65522D43C2339B63AB3D545428E20265039A034B8F641A9252DE32D62BF0BEBE1D54\nB17C70419B9055DA715521B9B66890195FBCAAB4550EE6814CA3BEBC64D7590059BD0F6A571AA6A0D51A0A80D3090673\n5A51E2DD2966ACA08629212B7A6D9E3A68D0A3DCA72B85A04CD4F0C5C443E4CF0C198130B6F6BE71F5AC25AACF429006\n641B4529FD3AA3B60B9D299FFA31B86DD8EC43F5927E3522E0C3D309066171DAE8360A19F62381CB89E0676EFEB1E647\n72635C2518E0B46585EFB51B26239089CCEEE301779563DFC4ACBFE637149915498A960291AA1D9821575E8796C7B587\n083F58065258178FABA84EA17A60B1695E9CBEE2D0C51259DF31EBD2195496E210118E68B41A2DD32FAB12F7FEF3A7F7\n61FCF77CCBFC878C6A1040297B30D60D134C71CD5EAB36A2F14C05ED5388E5FF8E71795DB5AFD3676DC4446BABC1A7AA\n38D8701E08E6D2367B881196DBD268D9FFD8502B3AA9CC451ACACDD205C6FCA0350CEE982B5CB2396A27128F97ECCB7B\nB6C027F6A74875098298CA3A5DE3960CA5D2B36CA4D11FAE9967B03DD69A7A3E008BFD4532F79F287C9403DB64AA4480\nD227AFB373875731EB08D9BA734D2C7703BFF50F473C22DA3FB9F19A1B228316EEF418FC08E83B301C0450AA4CE32853\nABDEF85F32D9E1787BF1C5A8CA85B69F891F40B82C88D7C1663445D646FD7BF372A3325523CFB5B079ABA0F1005CDBEE\n3F51AAAEC0898E47A5304E4BDDD6AED86D401C4E8EFB0C608D541E2F17B73AEDDEDC81F57285B7A639316F47508443C5\n11F36A268EBA7F819831FD136B83C911614864FAE3F5392C1211C16D4D0313A6C2E0DFF5328E5B35A77F08F785270D71\n9DB8CE9C1EBA773AF6A1A7269429C0201065756EEFAA320C66913A4E0E74E28AFEB6F817C7A7E4D835672EF083A89FA6\n281340A396DC498355E185ABBD4DED88FA3669A977595A9CD0A0B13DEB3116DC3E297B39015BD4FF5CE59EDAF755D53F\nE33B5176838E40AEE12EE83EF808B7B0242691AD824C2E2F377A34A105BD8C9A75525CCD5980CB92F8B1F8A5F22C9F4A\n59BFEF76A3744FE1C97C7F91D90D1205B28ED0E0BB46D45C442F656D7A1C0286FB7E7DB62A57B9DB80CD02BFE79E3521\nFBBE2813829FF074F79255DEF27BF2F27DF5A0140F994D25F4DC11177A776577CCBEEF9088E8FDB24E8EF526FE535D65\nA974470BCBE9E8719595876CFD8694A7E5FC20001E0A0AE3851724D4D0738A111E1EEF83E3D7E1BFCC98076D70373A8F\n3117554E60A8C8AB4F082D3776E62B58DD810FD16E9AA6553D8082999E2D169ADF4ECB3B5DDAA85308C7FF54DDC61131\n1AB6EBA303084AFBB445ECC07C0DC6CFCB1B7846888FF46A15622F1712E6416476589678DB29B56AAEDE63416FBE9B37\n6CC9D0EC1BF679179EFE790EB18228F20615C2BE969CE08180D700DB95874BC00D91555B1F86226474EA1B8985D2DDF7\n9FF1D9090664FA6D5972EFCE66A703D199E8DFAED7635F605FAB6EC522C83A946A3B0072F8DB90E705DCA2890F83AA03\nFE42141C8AE61C9EDBD8D0CA97216CADED0AE0A29EECC1FFD1B48A9AADAB340B133FB5188D859E0DF9FBAC212EDD7ADE\nBF9F7EBDBF84DFF5FD1EBEE11F0FF8189D73090229B75B267E4475044DB1AA2F3ADB463812D14135912906DFC9986992\n02F24812A971D2AE3B236D1CE26B8B75874A13A71F814D2965530A3A34CE6DE6318D7E4EDD256E7644823C47364CB9C4\n9BF44F84431156C294537EB02E36DAEB775FC164E2CA9FBE29D8063653D06F8219DABC8C5F4D45E721379E90A6D433A8\n644DECBC905EFE8E8BCA177CFFAC96BB21CF3D24713BC2A1746885CF328E7F6339C5E78EA5E0CD3AF59AB8FD43D44339\n088E45765FDFE917545912EDD0E93D6F3F02148A0A479AD1E7FA4EA1410050EF609D4DC1CA879840E7B20F76C09D71EF\nD74693C12B9F11B8F905ACEDA7726BF5119B3E0A04217D06D746767BADAE9D95A6476805ADF5387CC7A55ACAB2CB4818\nC1F262559836390880C528B106E4FB46113C38A14F1CFEA181B7FCDB94B07AFEB574F1BB92AAFFB0FE1E318BC6BCF04F\n1AFE91C57A9C73094A329051018B12C020CA3CCB1483D3C77C5A1279EE561A36C409E23EDCE8CEF1C1A19E99DA644FCF\n1ED62B7027863ECFBE751C399BF95363C16B58CC71D2074188BB147096F168CE1375FEF4A0C885A2671849560D07941D\n7461890C32499D0D94734AAB1AE90FE0BAB64A34F9331DB371C2B864D70BCB19F7BDE0693E2496B1C428095F58AE8AC0\n839919644D443755A69BA1425084B81829B52191582388EB8F134A2409EC0F6D7DAF3EFCF7F39F343915C48403BB7E67\n395F2A2C6794F4A6B5023F4556790C2A9B257767C23BCCF2713B4F832A8D8C530D184954CA580EBE8B3A5374FC6F4728\n078EC1F553D3344B0805FFE91429401B57AD77ECE8DADA3555A77803564C7CB2ED3BB5616591DF41B45DC9B79B138241\n15D7B36E1CC815B4F0F33F914BA1C8907891395A2155DA6AE12CBAC93869F6AEA82B8CB714C1358235A0784756C09AA7\n7F74146485F1B748BC558C6AA4951CCBF352F9546115275643D02795E335AA39DC2338DAEF1F27653AABF7CCBB25DB00\n363496D1F7C4EC4437427E171867C89C9A5B39085C3CF492F1163188FA12449E79271CC20B46ACCD1F39B89F9A56340A\n8586C2B1B19B31CE4757053EA7AE3F3E012DC5B9C1CBBAAB0A2AD271E4ECF80A7185CCA1CA6EEF9D8722385D8081F71A\n6C317B8286BD7F109D89B6F7AFE4410D4F97288034063E193A2160ED5418020F2FD5D53BA5870121381BA6993228E98D\n6F02356085BD64C4B0267E68D1E697B5326EB24FEB064C4DC2978E6B3022C0B43D47937867AC2742DD5C3C27ED0A6CE4\n4A0D0FDF5263A6707609F02E58F605B2DFEEC91FCB1D110CA18B1926B8102C8148FF98EF30360C01C54AD9AC057289C7\n3FD64DE017BABAB3D3E81B0C8CC8DF6BFE7EBA91FDF6A0CB5919B0012FD70BA0620F5FCE74B8EB4289B5BECAC9EFDA9A\nBBC6661BE065EED43ACED9CC0EBB8550414501BA1B29116F34115503DD0CB599563A934D4D956DCEC351E015543EFF2F\nA3DA59EC3D592D62FC6439D67BC880781DD7FDE80B5D8AED1A9D98CBC2EE784730AD8F64A5821223DAB33ECA4C857A80\nD59F4620D6EED1F933FA1FC59C8EF91E6651A54668DCB77FA85ADEE618D78C2B5DEAA8EC6B8B48C1925AC1B16A5E3782\n224B6AB6F040168916A581F8D41B20268635E5ADC1016EC9B5D069C50B3108515D35FC74F513047AF45710535BA4CC8B\n218282154B8C3D6BDA9185CBD6CF0580D0F0CF0DDF7AB499C7F8D54C765630E965B65860C1C0398A4254BC4A488BA1D9\n5C32057A1CBB50515B7FC7752D6855E6837BC398FDE6D5B8DAA8310178F5608B1AD2FD513447FAAF23AEE2DE15A70766\n69359A406155259823542A50C97DA6CE74F8190C8E63E5492FF91705FD391555F4B091BF60B7B2402E7AD36886C0FC38\n88ABB9038A04051A9F61AEF2D3B8A429F85143CF84264A906E1327AF7B52DBF900E8AEC0B56F64035720597CF5E165A8\n47C3BDEE722A85E2708DEA9D98D42AD570A2E976A2DAE67CB0F714D923B688C0B36F4212F4690C1581D6F70BB71BDF15\nE675631353B32043799034E3344880D686BB45A285DDF823643BD568AB995334C6250A877317375639BA8C0E39244BCC\nAA98840C2F27E6E2AC86345D1E25AEFD1EFF3C27AD26184A1AE509615D835F2CDC41A7C607555BB50B71FE86E730A1BC\n27AF5F24511ADD20F6329E3D646FDC43652A80CB95C4B6F0E1F3CF6CF2C29CEA81880C2DD2DA7482C6A51E98D3BC71ED\nE20B05DABB0EFA350A2CD5C862E7B1AF95146C837DF1CE9F136BD868C9A5F5872EA58FD75CB2C69937315AA4D0E243DF\nC8BEBD10C0D8226395461EE78CA861E474026CB430F3061511E62A3A0D3B2FB93BB383401879FB3938B7CE4DBAF69EAA\nE18F321CB168DD5C2C376561733DC63456CDEABC776AA17D6AF1F978AF0FD9C2AAD3D7A82DA86EBC198396B5A33EB3B2\n5C54AD77CE1DE5D5AAB30D367A327D5CA360668D84A0BD4F0FA90989B8EC148A2B2B748E75775A8EB251D026D6068C9A\nCA31D69417F014D7431C820C0083E675055C52AB0C388FA3357752E83E3BCB4881E325B1A94012764F16F1CE3DD72389\n44D73F247EB74666C1167A17B22A99F1AC3CC99DC5FE89BEBF2C68BC2CA7F1C52F261ECCD1AF7DAA7DC5944A4DC48797\n2D2B6A5E5EBF398218AB8CB9DC8083A1D180D265FE2ECC6AF10284B2366037244E5E57ADA5C5501A5EA45C31B6936057\nACEBED653FBFEAC708CA130093E5E679F63720CAB46E399E834F158B15CDE78C9093B085919BAE21EF03D0A4B62AB4C6\nD307049254728EEC2EB3476CCE42067FE05B96F2488BFA8F83E24710A5B730F868B0FD02746F4871D7F12EDFA1526176\n9947BE0A2FF8F2699DAD03FAE684A7CF357D8F5FC5A69B216635BC58D589B5E09F11F0A88A1FC83C24B2B7F16C8ADB3B\n397ACAD0EF15612272FDFC023DBD76359EE1C6D72CB259E103E0FF7A8703799F61ABCC4998C241CF6E9BAA529BD008B5\n9E23F6C1398277165DD4E1B3ADA00C58F8E267006A0B4BD26CE1C56B9DBA3F4082C528B8C1607585EEC4FA04ED6264B6\n2910674B9BD66C0E06626483CAF02F2DB8F60AD7D76A1C5814BE1860802902CDF6B195A56D2E279C08E31FC5C2077F63\n7FDB82C6C685ACA6D24CF17FDB1DCF86205660C024E0C0420B4E005F8B7860FEEAEC6D31934970EB2A454F929B6C1728\nBB89FCC00784CCAD1B85F285185C3D5A6054AF039D9EE426D386AA0B7CA3329CC20F3AD43E1F5243A831E970FC0CB47C\nF5E3C76F11ED224C0C1B82CB72A495281AD41BE5C46ED7F1ECBF252CB89287A8D215793439C0BE0DC8682DF2D38E0109\n3C4894326989D5C05DE82CE6A697594B9AC661B09EDB81DCD3F947348400CA87BE5D6D56F301023BFFFFFFFF00000000"
)
_GBBQ_KEYS = bytes.fromhex(_GBBQ_KEY_HEX)


def _gbbq_f(value: int) -> int:
    """The round function used by TDX's published table transform."""
    a = struct.unpack_from("<I", _GBBQ_KEYS, ((value & 0x00FF0000) >> 16) * 4 + 0x448)[0]
    a = (a + struct.unpack_from("<I", _GBBQ_KEYS, (value >> 24) * 4 + 0x48)[0]) & MASK32
    a ^= struct.unpack_from("<I", _GBBQ_KEYS, ((value & 0x0000FF00) >> 8) * 4 + 0x848)[0]
    a = (a + struct.unpack_from("<I", _GBBQ_KEYS, (value & 0xFF) * 4 + 0xC48)[0]) & MASK32
    return a


def _gbbq_decrypt_pair(left: int, right: int) -> tuple[int, int]:
    num = (struct.unpack_from("<I", _GBBQ_KEYS, 0x44)[0] ^ left) & MASK32
    numold = right
    for j in range(0x40, 0x03, -4):
        eax = (_gbbq_f(num) ^ struct.unpack_from("<I", _GBBQ_KEYS, j)[0]) & MASK32
        num, numold = (numold ^ eax) & MASK32, num
    return (numold ^ struct.unpack_from("<I", _GBBQ_KEYS, 0)[0]) & MASK32, num


def _gbbq_encrypt_pair(left: int, right: int) -> tuple[int, int]:
    # Invert the Feistel rounds in the opposite direction.  This is used by
    # synthetic tests and is also useful for constructing deterministic probes.
    num, numold = right, (left ^ struct.unpack_from("<I", _GBBQ_KEYS, 0)[0]) & MASK32
    for j in range(0x04, 0x41, 4):
        old_num = numold
        numold = (num ^ (_gbbq_f(numold) ^ struct.unpack_from("<I", _GBBQ_KEYS, j)[0])) & MASK32
        num = old_num
    return (num ^ struct.unpack_from("<I", _GBBQ_KEYS, 0x44)[0]) & MASK32, numold


def encrypt_gbbq_bytes(clear_records: bytes) -> bytes:
    """Build a synthetic encrypted gbbq envelope from 29-byte records."""
    if len(clear_records) % GBBQ_RECORD_SIZE:
        raise ValueError("gbbq clear records must be 29 bytes each")
    out = bytearray(struct.pack("<I", len(clear_records) // GBBQ_RECORD_SIZE))
    for offset in range(0, len(clear_records), GBBQ_RECORD_SIZE):
        record = clear_records[offset:offset + GBBQ_RECORD_SIZE]
        for pair_offset in range(0, 24, 8):
            left, right = struct.unpack_from("<II", record, pair_offset)
            out.extend(struct.pack("<II", *_gbbq_encrypt_pair(left, right)))
        out.extend(record[24:])
    return bytes(out)


def decrypt_gbbq_bytes(data: bytes) -> bytes:
    """Decrypt an encrypted gbbq envelope into its 29-byte records."""
    if len(data) < 4:
        raise ValueError("gbbq is missing its record-count header")
    (count,) = struct.unpack_from("<I", data)
    expected = 4 + count * GBBQ_RECORD_SIZE
    if len(data) != expected:
        raise ValueError(f"gbbq length {len(data)} does not match count {count}")
    out = bytearray()
    pos = 4
    for _ in range(count):
        for _ in range(3):
            left, right = struct.unpack_from("<II", data, pos)
            out.extend(struct.pack("<II", *_gbbq_decrypt_pair(left, right)))
            pos += 8
        out.extend(data[pos:pos + 5])
        pos += 5
    return bytes(out)


def parse_gbbq_bytes(data: bytes) -> list[dict[str, Any]]:
    """Return share-capital/ex-rights rows from an encrypted local gbbq."""
    clear = decrypt_gbbq_bytes(data)
    rows: list[dict[str, Any]] = []
    for pos in range(0, len(clear), GBBQ_RECORD_SIZE):
        market, raw_code, stamp, category, c1, c2, c3, c4 = struct.unpack_from(
            "<B7sIBffff", clear, pos
        )
        code = raw_code.split(b"\0", 1)[0].decode("ascii", "replace")
        stamp_text = str(stamp)
        event_date = (
            f"{stamp_text[:4]}-{stamp_text[4:6]}-{stamp_text[6:8]}"
            if len(stamp_text) == 8 else stamp_text
        )
        rows.append({
            "market": market, "code": code, "event_date": event_date,
            "category": category, "category_name": GBBQ_CATEGORY_NAMES.get(category, "未知"),
            "cash_dividend": round(c1, 6), "rights_issue_price": round(c2, 6),
            "bonus_shares": round(c3, 6), "rights_issue_shares": round(c4, 6),
            "raw_values": (c1, c2, c3, c4),
        })
    return rows


def parse_tdxhy_cfg_bytes(data: bytes, *, encoding: str = "gbk") -> list[dict[str, Any]]:
    """Parse ``tdxhy.cfg`` stock-to-industry assignments."""
    rows = []
    for line in data.decode(encoding, "replace").splitlines():
        fields = line.rstrip("\r").split("|")
        if len(fields) < 3 or not fields[0] or not fields[1]:
            continue
        rows.append({
            "market": int(fields[0]) if fields[0].isdigit() else fields[0],
            "code": fields[1], "tdx_industry_code": fields[2],
            "sw_industry_code": fields[5] if len(fields) > 5 else (fields[-1] if len(fields) > 3 else ""),
            "fields": fields,
        })
    return rows


def map_tdxhy_to_incon(
    assignments: Sequence[Mapping[str, Any]], taxonomy: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Attach ``incon.dat`` names to TDX/CSRC industry assignments.

    The two files use different code namespaces, so the join is deliberately
    left-outer and keeps an absent taxonomy row as ``None`` instead of
    inventing a sector name.
    """
    names = {str(row.get("code", "")): row.get("name") for row in taxonomy}
    result = []
    for assignment in assignments:
        row = dict(assignment)
        row["tdx_industry_name"] = names.get(str(row.get("tdx_industry_code", "")))
        row["sw_industry_name"] = names.get(str(row.get("sw_industry_code", "")))
        result.append(row)
    return result


def parse_incon_dat_bytes(data: bytes, *, encoding: str = "gbk") -> list[dict[str, str]]:
    """Parse the sectioned ``incon.dat`` industry taxonomy tree."""
    rows: list[dict[str, str]] = []
    section = ""
    for line in data.decode(encoding, "replace").splitlines():
        line = line.strip("\x00\r ")
        if not line:
            continue
        if line.startswith("######"):
            section = line.strip("# ")
            continue
        fields = line.split("|", 1)
        if len(fields) == 2:
            rows.append({"section": section, "code": fields[0], "name": fields[1]})
    return rows


def parse_tnf_bytes(
    data: bytes, *, record_size: int = 314, code_size: int = 6, name_offset: int = 6,
    name_size: int = 8, encoding: str = "gbk", header_size: int | None = None,
) -> list[dict[str, str]]:
    """Parse ``shs.tnf``/``szs.tnf`` fixed security-name records.

    TDX versions have changed the private record tail; the stable public
    prefix is code + GBK name.  Callers may override offsets for a client build.
    """
    if record_size <= 0:
        raise ValueError("TNF record_size must be positive")
    if header_size is None:
        if len(data) % record_size == 0:
            header_size = 0
        elif len(data) >= 50 and (len(data) - 50) % record_size == 0:
            # Public shm.tnf/szm.tnf fixtures carry a 50-byte server/header
            # prefix before 314-byte security records.
            header_size, name_offset = 50, 23
        else:
            raise ValueError("TNF length is not a header plus record_size")
    if header_size < 0 or len(data) < header_size or (len(data) - header_size) % record_size:
        raise ValueError("TNF length is not a header plus record_size")
    rows = []
    for pos in range(header_size, len(data), record_size):
        record = data[pos:pos + record_size]
        code = record[:code_size].split(b"\0", 1)[0].decode("ascii", "replace").strip()
        name = record[name_offset:name_offset + name_size].split(b"\0", 1)[0].decode(encoding, "replace").strip()
        if code:
            rows.append({"code": code, "name": name})
    return rows


def parse_dbf_bytes(data: bytes, *, encoding: str = "gbk") -> dict[str, Any]:
    """Read a dBase III/IV table such as ``base.dbf`` without dependencies."""
    if len(data) < 33:
        raise ValueError("DBF header is truncated")
    record_count, header_size, record_size = struct.unpack_from("<IHH", data, 4)
    if header_size < 33 or record_size < 1 or header_size > len(data):
        raise ValueError("invalid DBF header dimensions")
    fields = []
    pos = 32
    while pos + 32 <= len(data) and data[pos] != 0x0D:
        raw_name = data[pos:pos + 11].split(b"\0", 1)[0]
        fields.append({
            "name": raw_name.decode("ascii", "replace"), "type": chr(data[pos + 11]),
            "length": data[pos + 16], "decimals": data[pos + 17],
        })
        pos += 32
    if 1 + sum(field["length"] for field in fields) > record_size:
        raise ValueError("DBF field definitions exceed record size")
    records = []
    for index in range(min(record_count, (len(data) - header_size) // record_size)):
        row = data[header_size + index * record_size:header_size + (index + 1) * record_size]
        if not row or row[0] == 0x2A:
            continue
        cursor, values = 1, {}
        for field in fields:
            raw = row[cursor:cursor + field["length"]]
            cursor += field["length"]
            text = raw.decode(encoding, "replace").strip()
            values[field["name"]] = text
        records.append(values)
    return {"fields": fields, "records": records, "record_count": record_count}


def parse_base_dbf_bytes(data: bytes, *, encoding: str = "gbk") -> dict[str, Any]:
    return parse_dbf_bytes(data, encoding=encoding)


def parse_extended_daily_bytes(data: bytes, symbol: str, *, price_scale: float = 1.0) -> list[dict[str, Any]]:
    """Parse vipdoc ``ds``/HK/futures ``.day`` records.

    Extended-market records are ``<IffffIIf>``: date, four native-float OHLC
    values, an integer/bit-preserved amount field, volume, and settlement.
    ``amount_hk`` is the same amount word reinterpreted as a float, as done by
    the public ``tdxpy`` reader for HK files.  ``price_scale`` is available for
    client variants that store prices in integer ticks; standard extended files
    use native floats and therefore default to 1.0.
    """
    if len(data) % 32:
        raise ValueError("extended .day length is not a multiple of 32")
    rows = []
    for stamp, op, high, low, close, amount_raw, volume, settlement in struct.iter_unpack("<IffffIIf", data):
        stamp_text = str(stamp)
        if len(stamp_text) != 8 or close <= 0:
            continue
        amount_hk = struct.unpack("<f", struct.pack("<I", amount_raw))[0]
        rows.append({
            "ts_code": symbol, "trade_date": f"{stamp_text[:4]}-{stamp_text[4:6]}-{stamp_text[6:8]}",
            "open": round(op * price_scale, 6), "high": round(high * price_scale, 6),
            "low": round(low * price_scale, 6), "close": round(close * price_scale, 6),
            "volume": volume, "amount": amount_raw, "amount_hk": round(amount_hk, 6),
            "settlement": round(settlement * price_scale, 6), "price_scale": price_scale,
        })
    return rows


def parse_zxg_bytes(data: bytes, *, encoding: str = "gb2312") -> list[dict[str, str]]:
    """Parse a user ``zxg.blk``/``*.blk`` list retaining market and code."""
    rows = []
    for line in data.decode(encoding, "replace").splitlines():
        token = line.strip("\x00\r \t")
        if not token:
            continue
        if token[0] in "012" and token[1:].isdigit():
            rows.append({"market": token[0], "code": token[1:]})
        elif token.startswith(("1#", "0#", "2#")) and token[2:].isdigit():
            rows.append({"market": token[0], "code": token[2:]})
    return rows


def parse_block_members_bytes(data: bytes, *, encoding: str = "gb2312") -> list[str]:
    """Parse ``zxg.blk``/a user ``*.blk`` member list (market digit + code)."""
    result = []
    for line in data.decode(encoding, "replace").splitlines():
        token = line.strip("\x00\r \t")
        if token and token[0] in "012" and token[1:].isdigit():
            result.append(token[1:])
        elif token.isdigit():
            result.append(token)
    return result


def parse_blocknew_cfg_bytes(data: bytes, *, encoding: str = "gbk") -> list[dict[str, str]]:
    """Parse T0002/blocknew/blocknew.cfg name/file-id pairs."""
    if len(data) % 120:
        raise ValueError("blocknew.cfg length is not a multiple of 120")
    rows = []
    for pos in range(0, len(data), 120):
        name = data[pos:pos + 50].split(b"\0", 1)[0].decode(encoding, "replace").strip()
        file_id = data[pos + 50:pos + 120].split(b"\0", 1)[0].decode(encoding, "replace").strip()
        if name or file_id:
            rows.append({"blockname": name, "file_id": file_id})
    return rows


__all__ = [
    "GBBQ_CATEGORY_NAMES", "decrypt_gbbq_bytes", "encrypt_gbbq_bytes", "parse_gbbq_bytes",
    "parse_tdxhy_cfg_bytes", "parse_incon_dat_bytes", "parse_tnf_bytes", "parse_dbf_bytes",
    "map_tdxhy_to_incon", "parse_base_dbf_bytes", "parse_extended_daily_bytes", "parse_zxg_bytes",
    "parse_block_members_bytes",
    "parse_blocknew_cfg_bytes",
]
