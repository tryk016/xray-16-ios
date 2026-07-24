#pragma once
#include "xrCore/XML/XMLDocument.hpp"

class XRUICORE_API CUIXml : public XMLDocument
{
    int m_dbg_id;

public:
    CUIXml();
    virtual ~CUIXml();

    bool Load(pcstr path_alias, pcstr xml_filename, bool fatal = true);
    bool Load(pcstr path_alias, pcstr path, pcstr xml_filename, bool fatal = true);
    bool Load(pcstr path_alias, pcstr path, pcstr path2, pcstr xml_filename, bool fatal = true);

    virtual shared_str correct_file_name(pcstr path, pcstr fn);
};
